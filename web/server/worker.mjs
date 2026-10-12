import { discoverEnvironment, requireEnvironment } from './environment.mjs';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, writeFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createClient, createAdminClient } from '@insforge/sdk';
import { atomicJSON, readJSON, pendingJobs, lockJobs, alive, removeJobDirectory } from './jobs.mjs';
import { createUploads } from './uploads.mjs';

export class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
const root = fileURLToPath(new URL('../../', import.meta.url));
const jobRoot = fileURLToPath(new URL('./.jobs/', import.meta.url));
export async function validateRepository(input, request = fetch) {
  const match = typeof input === 'string' && input.trim().match(/^https:\/\/github\.com\/([A-Za-z0-9-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/);
  if (!match || ['.', '..'].includes(match[2])) throw new HttpError(400, 'Enter a public GitHub repository URL.');
  const api = `https://api.github.com/repos/${encodeURIComponent(match[1])}/${encodeURIComponent(match[2])}`;
  const get = async (url, missing) => {
    let response;
    try { response = await request(url, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'EBKit' }, redirect: 'error', signal: AbortSignal.timeout(10000) }); }
    catch { throw new HttpError(502, 'GitHub could not be reached. Try again.'); }
    if (response.status === 404) throw new HttpError(422, missing);
    if (response.status === 403 || response.status === 429) throw new HttpError(429, 'GitHub request limit reached. Please try again later.');
    if (!response.ok) throw new HttpError(502, 'GitHub validation failed. Try again.');
    return response.json();
  };
  const repository = await get(api, 'Repository was not found. It must be public.');
  if (repository.private || repository.archived || !repository.default_branch) throw new HttpError(422, 'Use an active public repository with a default branch.');
  const dockerfile = await get(`${api}/contents/Dockerfile?ref=${encodeURIComponent(repository.default_branch)}`, 'No root Dockerfile found. Run ebkit init locally and push the Dockerfile first.');
  if (dockerfile.type !== 'file' || dockerfile.size < 1 || dockerfile.size > 262144) throw new HttpError(422, 'The root Dockerfile must be a nonempty file smaller than 256 KB.');
  const detected = await discoverEnvironment(api, repository.default_branch, get);
  return { repository: `https://github.com/${match[1]}/${match[2]}`, branch: repository.default_branch, dockerfile: 'Dockerfile', size: dockerfile.size, ...detected };
}

export function commandArguments(operation, project, body = {}) {
  const target = ['--app', project.application_name, '--environment', project.environment_name, '--region', project.region];
  if (operation === 'deploy') return ['-m', 'ebkit.cli', 'deploy', body.sourceArchive || project.repository, ...target, '--port', String(project.port), '--yes', ...(body.sourceArchive ? ['--reviewed'] : [])];
  if (operation === 'scale') {
    if (!Number.isInteger(body.replicas) || body.replicas < 1 || body.replicas > 100) throw new HttpError(400, 'Replica count must be between 1 and 100.');
    return ['-m', 'ebkit.cli', 'scale', ...target, '--min', String(body.replicas), '--max', String(body.replicas), '--yes'];
  }
  if (operation === 'rollback') {
    if (typeof body.version !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(body.version)) throw new HttpError(400, 'Choose an existing version label.');
    return ['-m', 'ebkit.cli', 'rollback', ...target, '--version', body.version, '--yes'];
  }
  if (operation === 'destroy' && body.confirmation !== project.environment_name) throw new HttpError(400, 'Type the exact environment name to confirm destruction.');
  if (!['status','logs','versions','resources','cleanup-status','destroy'].includes(operation)) throw new HttpError(404, 'Unknown operation.');
  return ['-m', 'ebkit.cli', operation, ...target, ...(operation === 'status' ? ['--json'] : [])];
}

export function redact(text, secrets = []) {
  let safe = text.replace(/\x1b\[[0-9;]*m/g, '');
  for (const value of secrets.filter(Boolean).sort((a,b) => b.length-a.length)) safe = safe.split(value).join('[REDACTED]');
  return safe.replace(/(?:AKIA|ASIA)[A-Z0-9]{16}/g, '[REDACTED AWS KEY]');
}

export async function createWorker(config, dependencies = {}) {
  const admin = dependencies.admin ?? (config.apiKey && createAdminClient({ baseUrl: config.baseUrl, apiKey: config.apiKey }));
  const processRunner = dependencies.spawn ?? spawn;
  const authenticate = dependencies.authenticate ?? (async token => {
    const client = createClient({ baseUrl: config.baseUrl, anonKey: config.anonKey, accessToken: token, isServerMode: true });
    const { data, error } = await client.auth.getCurrentUser();
    if (error || !data?.user) throw new HttpError(401, 'Log in to continue.');
    return data.user;
  });
  const running = new Map();
  const directoryRoot = config.jobRoot ?? jobRoot;
  const uploads = createUploads({ ...config, jobRoot: directoryRoot }, dependencies);
  const durable = dependencies.durableJobs ?? !dependencies.spawn;
  const recover = dependencies.recover ?? !!(config.liveEnabled && admin);
  const monitors = new Set();
  const pollingTasks = new Set();
  let closing = false;
  const release = recover ? await lockJobs(directoryRoot) : async () => {};
  let launches = 0, zipReplacing = false;
  const replacementAuthorization=Symbol('ZIP replacement');
  async function save(id, changes) { const { error } = await admin.database.from('ebkit_web_projects').update(changes).eq('id', id); if (error) throw new HttpError(503, 'Could not save operation status.'); }
  async function authorized(req) {
    if (!config.liveEnabled || !admin) throw new HttpError(503, 'Live deployment worker is not configured.');
    const token = req.headers.authorization?.match(/^Bearer (.+)$/)?.[1];
    if (!token) throw new HttpError(401, 'Log in to continue.');
    const user = await authenticate(token);
    if (!config.allowedEmails.includes(user.email?.toLowerCase())) throw new HttpError(403, 'This account is not enabled for deployments on this AWS account.');
    return user;
  }
  async function own(id, user) {
    if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(404, 'Project not found.');
    const { data, error } = await admin.database.from('ebkit_web_projects').select('id,user_id,name,repository,region,port,mode,status,application_name,environment_name,replicas,revision').eq('id', id).eq('user_id', user.id).limit(1);
    if (error) throw new HttpError(503, 'Could not load project.');
    if (!data?.[0] || data[0].mode !== 'live') throw new HttpError(404, 'Live project not found.');
    return data[0];
  }
  async function run(operation, project, body = {}, recovery = null) {
    if(zipReplacing&&body[replacementAuthorization]!==true)throw new HttpError(409,'A ZIP replacement is being prepared. Wait for it to finish.');
    if (running.size) throw new HttpError(409, 'Another cloud operation is running. Wait for it to finish.');
    const args = recovery ? [] : (dependencies.commandArguments ?? commandArguments)(operation, project, body);
    let envText = '', secrets = [];
    if (body.environment) {
      if (operation !== 'deploy' || typeof body.environment !== 'object' || Array.isArray(body.environment)) throw new HttpError(400, 'Invalid environment settings.');
      const entries = Object.entries(body.environment);
      if (entries.length > 100 || entries.some(([key,value]) => !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || typeof value !== 'string' || /[\r\n\0]/.test(value) || value.length > 4096)) throw new HttpError(400, 'Use up to 100 valid environment settings with single-line values.');
      if (entries.some(([,value]) => value.includes('"'))) throw new HttpError(400, 'Environment values containing double quotes are not supported yet.');
      envText = entries.map(([key,value]) => `${key}="${value}"`).join('\n'); secrets = entries.map(([,value]) => value);
    }
    running.set(project.id, true);
    let directory, child, journalFile, journal;
    if (recovery) {
      journalFile = recovery.file; journal = recovery.job; directory = journal.directory;
    } else {
    try {
      await mkdir(directoryRoot, { recursive: true, mode: 0o700 });
      directory = await mkdtemp(path.join(directoryRoot, 'operation-'));
      if (envText) { const secretFile = path.join(directory, '.env'); await writeFile(secretFile, envText, { mode: 0o600 }); args.push('--env-file', secretFile); }
      const changes = { operation_log: `Starting ${operation}...\n`, last_error: null, last_operation: operation, operation_running: true };
      if (operation === 'deploy') Object.assign(changes, { status: 'deploying', stage: 2 });
      if (operation === 'destroy') changes.status = 'destroying';
      const childEnv = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', EBKIT_STATE_FILE: path.join(directoryRoot, `${project.id}.json`), EBKIT_CONFIG_FILE: path.join(directory, 'config') };
      delete childEnv.INSFORGE_API_KEY;
      delete childEnv.GEMINI_API_KEY; delete childEnv.GOOGLE_API_KEY;
      if (durable) {
        journalFile = path.join(directoryRoot, `journal-${project.id}.json`);
        journal = { id: randomUUID(), project, operation, body: { replicas: body.replicas, version: body.version }, directory, inputFile: path.join(directory, 'input.json'), state: 'prepared', started: Date.now(), deadline: Date.now() + 7200000, applied: false };
        const env = Object.fromEntries(['PYTHONUNBUFFERED','PYTHONIOENCODING','PYTHONUTF8','EBKIT_STATE_FILE','EBKIT_CONFIG_FILE'].map(key => [key, childEnv[key]]));
        await atomicJSON(journal.inputFile, { python: config.python, args, cwd: root, env, secretFile: envText ? path.join(directory, '.env') : null, stdin: operation === 'destroy' ? `${project.environment_name}\n` : '' });
        await atomicJSON(journalFile, journal);
      }
      await save(project.id, changes);
      if (durable) {
        const runner = processRunner(process.execPath, [fileURLToPath(new URL('./job-runner.mjs', import.meta.url)), journalFile], { cwd: root, shell: false, windowsHide: true, detached: true, env: childEnv, stdio: 'ignore' });
        await new Promise((resolve, reject) => { runner.once('spawn', resolve); runner.once('error', reject); });
        runner.unref();
      } else {
      child = processRunner(config.python, args, { cwd: root, shell: false, windowsHide: true, env: childEnv, stdio: ['pipe','pipe','pipe'] });
      child.stdin.end(operation === 'destroy' ? `${project.environment_name}\n` : '');
      }
    } catch (error) {
      running.delete(project.id);
      if (journalFile) await atomicJSON(journalFile, { ...journal, state: 'finished', code: 1, problem: 'The job could not start. Check status before retrying.' });
      if (directory) await removeJobDirectory(directoryRoot, directory);
      // A saved running flag must never survive a failed spawn.
      try {
        await save(project.id, { operation_running: false, last_error: 'The operation could not start.', ...(['deploy','destroy'].includes(operation) ? { status: 'failed' } : {}) });
        if (journalFile) await atomicJSON(journalFile, { ...journal, state: 'finished', code: 1, applied: true });
      } catch {}
      throw error;
    }
    }
    let output = '', flushTimer, persistenceError = false;
    const append = chunk => { output = (output + chunk.toString()).slice(-48000); if (!flushTimer) flushTimer = setTimeout(() => { flushTimer = undefined; void save(project.id, { operation_log: redact(output, secrets) }).catch(() => { persistenceError = true; }); }, 1200); };
    if (child) { child.stdout.on('data', append); child.stderr.on('data', append); }
    const timeout = child ? setTimeout(() => child.kill(), 7200000) : null;
    let finished = false;
    const finish = async (code, problem) => {
      if (finished) return; finished = true; clearTimeout(timeout); clearTimeout(flushTimer);
      try {
        const changes = { operation_log: [redact(output, secrets), problem].filter(Boolean).join('\n') || 'Operation finished.', operation_running: false, last_error: code ? problem || 'Cloud operation failed. Review the operation log. Partial resources may remain.' : null };
        if (operation === 'deploy') {
          let record;
          try { record = JSON.parse(await readFile(path.join(directoryRoot, `${project.id}.json`), 'utf8')).deployments?.[0]; } catch {}
          const healthy = !code && record?.status === 'Ready' && record?.health === 'Green';
          Object.assign(changes, { status: healthy ? 'ready' : 'failed', stage: healthy ? 6 : 2, health: record?.health ?? null, live_url: record?.url ?? null, revision: healthy && project.status === 'ready' ? project.revision + 1 : project.revision });
          if (!healthy && !code) changes.last_error = 'AWS did not report Ready / Green. Check status and logs; resources may still be running.';
        } else if (operation === 'status' && !code) {
          let live;
          try { live = JSON.parse(output.trim()); } catch {}
          if (live?.environment === project.environment_name) {
            const status = live.status === 'Terminated' ? 'destroyed' : live.status === 'Terminating' ? 'destroying' : live.status === 'Ready' ? (live.health === 'Green' ? 'ready' : 'failed') : 'deploying';
            Object.assign(changes, { status, health: live.health, live_url: status === 'destroyed' ? null : live.url });
          }
        } else if (operation === 'destroy') {
          Object.assign(changes, { status: code ? 'failed' : 'destroyed', live_url: null });
          if (!code) await uploads.removeProject(project.id);
        }
        else if (operation === 'scale' && !code) changes.replicas = body.replicas;
        else if (operation === 'rollback' && !code) changes.revision = Math.max(1, project.revision - 1);
        await save(project.id, changes);
        if (journalFile) await atomicJSON(journalFile, { ...journal, applied: true });
      } catch {
        console.error('Operation status could not be persisted; recovery will retry.');
        if (journalFile) { finished = false; return false; }
      }
      running.delete(project.id);
      if (directory) await removeJobDirectory(directoryRoot, directory).catch(() => { console.error('Temporary job files could not be removed; check the private job directory.'); });
      if (persistenceError) console.error('Some intermediate operation logs could not be persisted.');
      return true;
    };
    if (journalFile) {
      let polling = false, previous = '';
      const poll = async () => {
        if (polling || finished || closing) return;
        polling = true;
        try {
          journal = await readJSON(journalFile);
          output = journal.output ?? '';
          if (journal.state !== 'finished' && !alive(journal.runnerPid) && !alive(journal.childPid) && Date.now() - journal.started > 15000) {
            journal = { ...journal, state: 'finished', code: 1, problem: 'Operation interrupted by a worker or host shutdown. Check Status and Resources before retrying; partial resources may remain.' };
            await atomicJSON(journalFile, journal);
          }
          if (journal.state === 'finished') {
            if (await finish(journal.code, journal.problem)) { clearInterval(timer); monitors.delete(timer); }
          } else if (output !== previous) {
            await save(project.id, { operation_log: output, operation_running: true }); previous = output;
          }
        } catch { console.error('Job recovery could not persist progress; it will retry.'); }
        finally { polling = false; }
      };
      const watch = () => {
        const task = poll(); pollingTasks.add(task);
        void task.finally(() => pollingTasks.delete(task));
        return task;
      };
      const timer = setInterval(() => void watch(), dependencies.pollInterval ?? 1200);
      monitors.add(timer);
      await watch();
      return { accepted: true, projectId: project.id, operation };
    }
    child.once('error', () => void finish(1, 'Could not start EBKit. Check EBKIT_PYTHON and the CLI installation.'));
    child.once('close', code => void finish(code ?? 1));
    return { accepted: true, projectId: project.id, operation };
  }
  if (recover) {
    try {
      const pending = await pendingJobs(directoryRoot);
      // Pilot worker is serial; more than one running job is unsafe to adopt.
      if (pending.length > 1) throw new Error('Multiple unfinished jobs require operator review.');
      for (const item of pending) await run(item.job.operation, item.job.project, item.job.body, item);
      const { data, error } = await admin.database.from('ebkit_web_projects').select('id,status,operation_log').eq('mode', 'live').eq('operation_running', true).limit(1001);
      if (error || data.length > 1000) throw new Error('Could not check interrupted project operations.');
      for (const project of data) {
        if (running.has(project.id)) continue;
        await save(project.id, { operation_running: false, last_error: 'Operation interrupted. Check Status and Resources before retrying; partial resources may remain.', operation_log: `${project.operation_log ?? ''}\nOperation interrupted. Check Status and Resources before retrying.`, ...(['deploying','destroying'].includes(project.status) ? { status: 'failed' } : {}) });
      }
    } catch (error) { for (const timer of monitors) clearInterval(timer); await release(); throw error; }
  }
  const server = createServer(async (req, res) => {
    let ownsZipReplacement=false;
    const reply = (status, value) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
    try {
      const route = new URL(req.url, 'http://localhost').pathname;
      if (req.method === 'GET' && route === '/api/health') return reply(200, { liveEnabled: !!(config.liveEnabled && admin && config.allowedEmails.length), awsCallsDuringStartup: false, recoveringJobs: running.size });
      const origin = req.headers.origin;
      if (origin && !config.origins.includes(origin)) throw new HttpError(403, 'Request origin is not allowed.');
      if (req.method !== 'POST') throw new HttpError(405, 'POST required.');
      if (route === '/api/uploads') {
        const user = await authorized(req);
        if (!req.headers['content-type']?.startsWith('application/zip')) throw new HttpError(415,'Upload a ZIP using application/zip.');
        return reply(200, await uploads.upload(req, user, req.headers['x-ebkit-filename']));
      }
      let raw = ''; for await (const chunk of req) { raw += chunk; if (Buffer.byteLength(raw) > 65536) throw new HttpError(413, 'Request is too large.'); }
      let body; try { body = JSON.parse(raw || '{}'); } catch { throw new HttpError(400, 'Invalid JSON.'); }
      if (route === '/api/repository') return reply(200, await validateRepository(body.repository, dependencies.fetch));
      const user = await authorized(req);
      if ('sourceArchive' in body) throw new HttpError(400,'Local source paths cannot be submitted through the API.');
      const uploadRoute = route.match(/^\/api\/uploads\/([0-9a-f-]+)\/(prepare|download)$/i);
      if (uploadRoute) {
        if (uploadRoute[2] === 'prepare') return reply(200, await uploads.prepare(uploadRoute[1], user, body.port, body.allowAI, body.skipScout === true, body.acknowledgeScoutBypass === true));
        const item = await uploads.reviewed(uploadRoute[1], user, body, false);
        const archive = await uploads.archive(item);
        const data = await readFile(archive);
        res.writeHead(200, {'Content-Type':'application/zip','Content-Disposition':'attachment; filename="ebkit-prepared.zip"','Cache-Control':'no-store'});
        return res.end(data);
      }
      if (route === '/api/deploy') {
        if (body.confirmCosts !== true) throw new HttpError(400, 'Confirm that AWS resources incur charges.');
        if (running.size||zipReplacing) throw new HttpError(409, 'Another cloud operation is running.');
        if (launches >= 5) throw new HttpError(429, 'Worker launch limit reached. Ask the operator to review existing resources.');
        let zipItem;
        const repository = body.uploadId ? await (async () => {
          zipItem = await uploads.reviewed(body.uploadId, user, body);
          if (zipItem.record.projectId) throw new HttpError(409,'This ZIP is already attached to a project.');
          if(body.port!==zipItem.record.port)throw new HttpError(400,'Port changed after ZIP preparation. Prepare and review the Dockerfile again.');
          const inspected = await readJSON(path.join(zipItem.directory,'inspection.json'));
          const { inspectEnvironment } = await import('./environment.mjs');
          return { repository: `zip:${zipItem.record.name}`, environment: inspectEnvironment(inspected.files) };
        })() : await validateRepository(body.repository, dependencies.fetch);
        const missing = requireEnvironment(repository.environment, body.environment);
        if (missing.length) throw new HttpError(400, `Fill in required environment settings: ${missing.join(', ')}.`);
        if (!/^[a-z][a-z0-9-]{1,39}$/.test(body.name) || !['us-east-1','us-east-2','us-west-2','ap-south-1'].includes(body.region) || !Number.isInteger(body.port) || body.port < 1 || body.port > 65535) throw new HttpError(400, 'Invalid project name, region or port.');
        const { data: existing, error: quotaError } = await admin.database.from('ebkit_web_projects').select('id').eq('user_id', user.id).eq('mode','live').neq('status','destroyed').limit(3);
        if (quotaError) throw new HttpError(503, 'Could not check deployment quota.');
        if (existing.length >= 3) throw new HttpError(429, 'Destroy an existing live project before creating another.');
        if (running.size) throw new HttpError(409,'Another cloud operation is running.');
        const id = randomUUID(); const application = `ebk-${id.replaceAll('-','').slice(0,16)}`;
        const project = { id, user_id: user.id, name: body.name, repository: repository.repository, region: body.region, port: body.port, mode: 'live', status: 'deploying', stage: 1, replicas: 1, revision: 1, variable_names: Object.keys(body.environment ?? {}), application_name: application, environment_name: `${application}-cluster` };
        const { data, error } = await admin.database.from('ebkit_web_projects').insert([project]).select('*');
        if (error) throw new HttpError(503, 'Could not save deployment.');
        try {
          if (zipItem) {
            body.sourceArchive = await uploads.archive(zipItem);
            zipItem.record.projectId = id;
            await atomicJSON(path.join(zipItem.directory,'record.json'),zipItem.record);
            await atomicJSON(path.join(directoryRoot,`${id}.source.json`),{ uploadId:body.uploadId, reviewHash:body.reviewHash });
          }
          await run('deploy', project, body); launches++;
        } catch (error) { await save(id, { status: 'failed', last_error: error.message }); throw error; }
        return reply(202, { project: data[0] });
      }
      const match = route.match(/^\/api\/projects\/([0-9a-f-]+)\/([a-z-]+)$/i);
      if (!match) throw new HttpError(404, 'Endpoint not found.');
      const project = await own(match[1], user);
      if (match[2] === 'deploy' && body.confirmCosts !== true) throw new HttpError(400, 'Confirm AWS charges for redeployment.');
      if (match[2] === 'deploy') {
        if (project.repository.startsWith('zip:')) {
          if(running.size||zipReplacing)throw new HttpError(409,'Another cloud operation is running. Wait for it to finish.');
          if(body.uploadId){zipReplacing=true;ownsZipReplacement=true;}
          if(ownsZipReplacement)body[replacementAuthorization]=true;
          let item;
          if(body.uploadId){
            if(!['ready','failed'].includes(project.status))throw new HttpError(409,'Only an existing ready or failed ZIP deployment can be updated.');
            item=await uploads.reviewed(body.uploadId,user,body);
            if(item.record.projectId)throw new HttpError(409,'This ZIP is already attached to a project.');
            if(body.port!==item.record.port)throw new HttpError(400,'Port changed after ZIP preparation. Prepare and review the Dockerfile again.');
            const inspected=await readJSON(path.join(item.directory,'inspection.json'));
            const {inspectEnvironment}=await import('./environment.mjs');
            const missing=requireEnvironment(inspectEnvironment(inspected.files),body.environment);
            if(missing.length)throw new HttpError(400,`Fill in required environment settings: ${missing.join(', ')}.`);
            body.sourceArchive=await uploads.archive(item);
            if(running.size)throw new HttpError(409,'Another cloud operation is running. Wait for it to finish.');
            item.record.projectId=project.id;
            await atomicJSON(path.join(item.directory,'record.json'),item.record);
            await atomicJSON(path.join(directoryRoot,`${project.id}.source.json`),{uploadId:body.uploadId,reviewHash:body.reviewHash});
            const changes={repository:`zip:${item.record.name}`,port:item.record.port,variable_names:Object.keys(body.environment??{})};
            await save(project.id,changes);Object.assign(project,changes);
          }else{
            const source=await readJSON(path.join(directoryRoot,`${project.id}.source.json`)).catch(()=>null);
            if(!source)throw new HttpError(410,'ZIP source is unavailable. Upload a new ZIP to create a deployment.');
            item=await uploads.reviewed(source.uploadId,user,{reviewed:true,reviewHash:source.reviewHash});
            if(item.record.projectId!==project.id)throw new HttpError(409,'ZIP source does not belong to this project.');
            body.sourceArchive=await uploads.archive(item);
          }
        } else {if(body.uploadId)throw new HttpError(400,'ZIP replacement is available only for ZIP projects.');await validateRepository(project.repository, dependencies.fetch);}
      }
      return reply(202, await run(match[2], project, body));
    } catch (error) { reply(error.status ?? 500, { error: error.status ? error.message : 'Backend request failed. Check worker configuration.' }); }
    finally {if(ownsZipReplacement)zipReplacing=false;}
  });
  const stopWatching = () => {
    closing = true;
    for (const timer of monitors) clearInterval(timer); monitors.clear();
    void Promise.allSettled([...pollingTasks]).then(release);
  };
  server.once('close', stopWatching); server.once('error', stopWatching);
  return server;
}
