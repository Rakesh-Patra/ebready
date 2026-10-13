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
import {createTrials,trialColumns} from './trials.mjs';
import {createGithubDeploys} from './github-deploys.mjs';

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
  if (operation === 'deploy') return ['-m', 'ebkit.cli', 'deploy', body.sourceArchive || project.repository, ...target, '--port', String(project.port), '--yes', ...(project.plan==='free_trial'?['--max-replicas','1']:[]), ...(body.sourceArchive ? ['--reviewed'] : []), ...(body.githubCommit ? ['--commit', body.githubCommit] : [])];
  if (operation === 'scale') {
    if (!Number.isInteger(body.replicas) || body.replicas < 1 || body.replicas > 100) throw new HttpError(400, 'Replica count must be between 1 and 100.');
    return ['-m', 'ebkit.cli', 'scale', ...target, '--min', String(body.replicas), '--max', String(body.replicas), '--yes'];
  }
  if (operation === 'rollback') {
    if (typeof body.version !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(body.version)) throw new HttpError(400, 'Choose an existing version label.');
    return ['-m', 'ebkit.cli', 'rollback', ...target, '--version', body.version, '--yes'];
  }
  if(operation==='production'){
    const actions=['metrics','alarm','autoscale','cost','budget','leftovers','database-create','database-status','database-network','database-attach','database-check','database-migrate','database-snapshot','database-delete','pipeline'];
    if(!actions.includes(body.action)||!body.configuration||typeof body.configuration!=='object'||Array.isArray(body.configuration))throw new HttpError(400,'Choose a supported production operation and JSON configuration.');
    return ['-m','ebkit.cli','operations',body.action,...target,'--yes'];
  }
  if(operation==='config'){
    if(body.unset!==undefined&&(!Array.isArray(body.unset)||body.unset.length>100||body.unset.some(key=>typeof key!=='string'||!/^[_A-Za-z][_A-Za-z0-9]*$/.test(key)||key==='PORT')))throw new HttpError(400,'Invalid variable names to remove.');
    return ['-m','ebkit.cli','config',...target,...(body.unset??[]).flatMap(key=>['--unset',key]),...(body.environment||body.unset?.length?['--yes']:[])];
  }
  if(operation==='options'){
    if(body.options!==undefined&&(!Array.isArray(body.options)||body.options.length>100||body.options.some(item=>!item||Object.keys(item).sort().join(',')!=='Namespace,OptionName,Value'||Object.values(item).some(value=>typeof value!=='string')||item.Value.length>4096)))throw new HttpError(400,'Invalid environment options.');
    if(body.removeOptions!==undefined&&(!Array.isArray(body.removeOptions)||body.removeOptions.length>100||body.removeOptions.some(value=>typeof value!=='string'||!/^[-A-Za-z0-9_:.]+\/[-A-Za-z0-9_./: ]{1,255}$/.test(value))))throw new HttpError(400,'Invalid option names to remove.');
    return ['-m','ebkit.cli','options',...target,...(body.removeOptions??[]).flatMap(value=>['--remove',value]),...(body.options||body.removeOptions?.length?['--yes']:['--json'])];
  }
  if(['config-save','config-apply'].includes(operation)){
    if(!/^[A-Za-z0-9-]{1,40}$/.test(body.templateName??''))throw new HttpError(400,'Invalid configuration template name.');
    return ['-m','ebkit.cli',operation,...target,'--name',`ebkit-${project.id}-${body.templateName}`,'--yes'];
  }
  if(operation==='version-delete'){
    if(!/^[A-Za-z0-9_.-]{1,100}$/.test(body.version??''))throw new HttpError(400,'Choose an existing version label.');
    return ['-m','ebkit.cli',operation,...target,'--version',body.version,'--yes'];
  }
  if(operation==='swap'){
    if(!/^[-A-Za-z0-9]{3,40}$/.test(body.destinationEnvironment??''))throw new HttpError(400,'Choose an owned destination project.');
    return ['-m','ebkit.cli','swap',...target,'--destination',body.destinationEnvironment,'--yes'];
  }
  if(operation==='clone'){
    if(!/^[-A-Za-z0-9]{3,40}$/.test(body.sourceEnvironment??''))throw new HttpError(400,'Invalid clone source.');
    return ['-m','ebkit.cli','clone','--app',project.application_name,'--environment',body.sourceEnvironment,'--region',project.region,'--name',project.environment_name,'--yes',...(project.plan==='free_trial'?['--max-replicas','1']:[])];
  }
  if (operation === 'destroy' && body.confirmation !== project.environment_name) throw new HttpError(400, 'Type the exact environment name to confirm destruction.');
  if (!['status','logs','versions','resources','cleanup-status','destroy','health','events'].includes(operation)) throw new HttpError(404, 'Unknown operation.');
  return ['-m', 'ebkit.cli', operation, ...target, ...(['status','cleanup-status','health','events'].includes(operation) ? ['--json'] : [])];
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
  const trials=config.trialsEnabled!==false&&config.liveEnabled&&admin?createTrials({admin,save,run,isBusy:()=>running.size>0||zipReplacing,capacity:config.globalCapacity??2,testingEmails:config.testingEmails??[],evaluatorEmails:config.evaluatorEmails??[],now:dependencies.now??Date.now}):null;
  const githubDeploys=config.githubAutoDeploy!==false&&config.liveEnabled&&admin?createGithubDeploys({
    directory:directoryRoot,request:dependencies.fetch,token:config.githubToken,
    isBusy:()=>closing||running.size>0||zipReplacing,
    eligible:project=>{try{trials?.guard(project,'deploy',{});return true;}catch{return false;}},
    list:async()=>{
      const {data,error}=await admin.database.from('ebkit_web_projects').select(`${trialColumns},operation_running,variable_names,live_url`).eq('mode','live').order('id',{ascending:true}).limit(101);
      if(error||!data||data.length>100)throw new Error('GitHub projects could not be loaded safely.');
      const states=await Promise.all(data.map(item=>readJSON(path.join(directoryRoot,`${item.id}.pipeline.json`)).catch(()=>null)));
      return data.filter((item,index)=>states[index]?.enabled!==true);
    },
    validate:async project=>{
      const repository=await validateRepository(project.repository,dependencies.fetch);
      const missing=requireEnvironment(repository.environment.filter(item=>!project.variable_names?.includes(item.name)),{});
      if(missing.length)throw new Error(`Automatic deployment needs new environment settings: ${missing.join(', ')}. Redeploy manually with these settings.`);
    },
    run:(project,head)=>run('deploy',project,{githubCommit:head.sha}),
    onError:async(project,error)=>{console.error(`GitHub auto-deploy check failed for ${project.id}: ${error.message}`);if(!running.size)await save(project.id,{last_error:`GitHub auto-deploy deferred: ${error.message}`}).catch(()=>{});}
  }):null;
  async function save(id, changes) { const { error } = await admin.database.from('ebkit_web_projects').update(changes).eq('id', id); if (error) throw new HttpError(503, 'Could not save operation status.'); }
  async function authorized(req) {
    if (!config.liveEnabled || !admin) throw new HttpError(503, 'Live deployment worker is not configured.');
    const token = req.headers.authorization?.match(/^Bearer (.+)$/)?.[1];
    if (!token||['null','undefined'].includes(token)||token.length>8192) throw new HttpError(401, 'Log in to continue.');
    let user;try{user=await authenticate(token);}catch{throw new HttpError(401,'Your session is invalid or expired. Log in again.');}
    if(!user?.id)throw new HttpError(401,'Log in to continue.');
    if (!config.allowedEmails.includes(user.email?.toLowerCase())) throw new HttpError(403, 'This account is not enabled for deployments on this AWS account.');
    return user;
  }
  async function own(id, user) {
    if (!/^[0-9a-f-]{36}$/i.test(id)) throw new HttpError(404, 'Project not found.');
    const { data, error } = await admin.database.from('ebkit_web_projects').select(`${trialColumns},variable_names,live_url,operation_running`).eq('id', id).eq('user_id', user.id).limit(1);
    if (error) throw new HttpError(503, 'Could not load project.');
    if (!data?.[0] || data[0].mode !== 'live') throw new HttpError(404, 'Live project not found.');
    return data[0];
  }
  async function run(operation, project, body = {}, recovery = null) {
    if(!recovery)trials?.guard(project,operation,body);
    if(zipReplacing&&body[replacementAuthorization]!==true)throw new HttpError(409,'A ZIP replacement is being prepared. Wait for it to finish.');
    if (running.size) throw new HttpError(409, 'Another cloud operation is running. Wait for it to finish.');
    if(body.profile&&(!(config.awsProfiles??[]).includes(body.profile)||!/^[-A-Za-z0-9_.]+$/.test(body.profile)||!/^\d{12}$/.test(config.awsAccountId??'')))throw new HttpError(403,'Choose a configured operator profile; the worker must have its AWS account ID configured.');
    const args = recovery ? [] : (dependencies.commandArguments ?? commandArguments)(operation, project, body);
    const deadline=project.plan==='free_trial'?(['deploy','scale','rollback','clone'].includes(operation)?Math.min(Date.now()+7200000,Date.parse(project.trial_expires_at)):Date.now()+(['destroy'].includes(operation)?7200000:60000)):Date.now()+7200000;
    if(body.profile&&!recovery)args.splice(2,0,'--profile',body.profile);
    let envText = '', secrets = [];
    if (body.environment) {
      if (!['deploy','config'].includes(operation) || typeof body.environment !== 'object' || Array.isArray(body.environment)) throw new HttpError(400, 'Invalid environment settings.');
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
      if(operation==='deploy'){const file=path.join(directoryRoot,`${project.id}.release.json`);if(body.releasePolicy)await atomicJSON(file,body.releasePolicy);else body.releasePolicy=await readJSON(file).catch(()=>null);}
      if(operation==='deploy'&&!body.sourceArchive&&project.repository.startsWith('https://github.com/')&&githubDeploys){
        if(body.githubCommit){await githubDeploys.record(project,{sha:body.githubCommit});}
        else{
          try{const head=await githubDeploys.head(project.repository);args.push('--commit',head.sha);await githubDeploys.record(project,head);}
          catch{console.error(`GitHub auto-deploy baseline could not be saved for ${project.id}; manual deployment continues.`);}
        }
      }
      await mkdir(directoryRoot, { recursive: true, mode: 0o700 });
      directory = await mkdtemp(path.join(directoryRoot, 'operation-'));
      if(body.configuration){const file=path.join(directory,'operation.json');await writeFile(file,JSON.stringify(body.configuration),{mode:0o600});args.push('--file',file);}
      if(body.releasePolicy){const file=path.join(directory,'release.json');await writeFile(file,JSON.stringify(body.releasePolicy),{mode:0o600});args.push('--release-policy',file);}
      if(body.options){const file=path.join(directory,'options.json');await writeFile(file,JSON.stringify(body.options),{mode:0o600});args.push('--file',file);secrets.push(...body.options.map(item=>item.Value));}
      if (envText) { const secretFile = path.join(directory, '.env'); await writeFile(secretFile, envText, { mode: 0o600 }); args.push('--env-file', secretFile); }
      const changes = { operation_log: `Starting ${operation}...\n`, last_error: null, last_operation: operation, operation_running: true };
      if (['deploy','clone'].includes(operation)) Object.assign(changes, { status: 'deploying', stage: 2 });
      if (operation === 'destroy') changes.status = 'destroying';
      const childEnv = { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', EBKIT_STATE_FILE: path.join(directoryRoot, `${project.id}.json`), EBKIT_CONFIG_FILE: path.join(directory, 'config') };
      if(body.profile)childEnv.AWS_PROFILE=body.profile;
      if(config.awsAccountId)childEnv.EBKIT_EXPECTED_AWS_ACCOUNT=config.awsAccountId;
      delete childEnv.INSFORGE_API_KEY;delete childEnv.EBKIT_GITHUB_TOKEN;
      delete childEnv.GEMINI_API_KEY; delete childEnv.GOOGLE_API_KEY;
      if (durable) {
        journalFile = path.join(directoryRoot, `journal-${project.id}.json`);
        journal = { id: randomUUID(), project, operation, body: { replicas: body.replicas, version: body.version,unset:body.unset,environmentNames:Object.keys(body.environment??{}),destinationId:body.destinationId,action:body.action }, directory, inputFile: path.join(directory, 'input.json'), state: 'prepared', started: Date.now(), deadline, applied: false };
        const env = Object.fromEntries(['PYTHONUNBUFFERED','PYTHONIOENCODING','PYTHONUTF8','EBKIT_STATE_FILE','EBKIT_CONFIG_FILE',...(config.awsAccountId?['EBKIT_EXPECTED_AWS_ACCOUNT']:[]),...(body.profile?['AWS_PROFILE']:[])].map(key => [key, childEnv[key]]));
        await atomicJSON(journal.inputFile, { python: config.python, args, cwd: root, env, secretFile: envText ? path.join(directory, '.env') : null, optionsFile:body.options?path.join(directory,'options.json'):null, stdin: operation === 'destroy' ? `${project.environment_name}\n` : '' });
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
    const timeout = child ? setTimeout(() => child.kill(), Math.max(1,deadline-Date.now())) : null;
    let finished = false;
    const finish = async (code, problem) => {
      if (finished) return; finished = true; clearTimeout(timeout); clearTimeout(flushTimer);
      try {
        const changes = { operation_log: [redact(output, secrets), problem].filter(Boolean).join('\n') || 'Operation finished.', operation_running: false, last_error: code ? problem || 'Cloud operation failed. Review the operation log. Partial resources may remain.' : null };
        if (['deploy','clone'].includes(operation)) {
          let record;
          try {const records=JSON.parse(await readFile(path.join(directoryRoot, `${project.id}.json`), 'utf8')).deployments??[];record=records.find(item=>item.environment_name===project.environment_name)??(records.length===1&&!records[0].environment_name?records[0]:undefined);} catch {}
          let healthy = !code && record?.status === 'Ready' && record?.health === 'Green' && record?.release_verification!=='failed';
          if(project.plan==='free_trial'&&healthy){const deadline=await trials.start(project.id);Object.assign(changes,deadline);healthy=!!deadline.trial_started_at&&Date.parse(deadline.trial_expires_at)>Date.now();}
          if(project.plan==='free_trial'&&!healthy)Object.assign(changes,{trial_cleanup_state:'pending',trial_cleanup_next_at:new Date().toISOString()});
          Object.assign(changes, { status: healthy ? 'ready' : 'failed', stage: healthy ? 6 : 2, health: record?.health ?? null, live_url: record?.url ?? project.live_url ?? null, revision: healthy && project.status === 'ready' ? project.revision + 1 : project.revision });
          if (!healthy && !code) changes.last_error = 'AWS did not report Ready / Green. Check status and logs; resources may still be running.';
        } else if (operation === 'status' && !code) {
          let live;
          try { live = JSON.parse(output.trim()); } catch {}
          if (live?.environment === project.environment_name) {
            const status = live.status === 'Terminated' ? 'destroyed' : live.status === 'Terminating' ? 'destroying' : live.status === 'Ready' ? (live.health === 'Green' ? 'ready' : 'failed') : 'deploying';
            Object.assign(changes, { status, health: live.health, live_url: status === 'destroyed' ? null : live.url });
          }
        } else if (operation === 'destroy') {
          Object.assign(changes, { status: code===2?'destroying':code ? 'failed' : 'destroyed', live_url: null });
          if(code===2)changes.last_error=null;
          if(project.plan==='free_trial')Object.assign(changes,{trial_cleanup_state:code?'pending':'verifying',trial_cleanup_next_at:new Date(Date.now()+(code?Math.min(1800000,60000*2**Math.min(project.trial_cleanup_attempts??0,5)):60000)).toISOString()});
          if (!code) await uploads.removeProject(project.id);
        }
        else if(operation==='cleanup-status'&&project.plan==='free_trial'){
          let report;try{report=JSON.parse(output.trim());}catch{}
          const complete=!code&&report?.environment===project.environment_name&&report?.cleanup_complete===true;
          Object.assign(changes,{trial_cleanup_state:complete?'complete':'verifying',trial_cleanup_next_at:complete?null:new Date(Date.now()+300000).toISOString()});
          if(!complete)changes.last_error='Cleanup verification is pending. AWS managed infrastructure may still incur charges.';
        }
        else if(operation==='production'&&!code&&body.action==='pipeline')await atomicJSON(path.join(directoryRoot,`${project.id}.pipeline.json`),{enabled:true});
        else if(operation==='production'&&!code&&body.action==='database-attach')changes.variable_names=[...new Set([...(project.variable_names??[]),'DATABASE_URL'])];
        else if(operation==='config'&&!code&&(body.environment||body.environmentNames?.length||body.unset?.length))changes.variable_names=[...new Set([...(project.variable_names??[]),...(body.environmentNames??Object.keys(body.environment??{}))])].filter(key=>!(body.unset??[]).includes(key));
        else if(operation==='swap'&&!code){changes.live_url=null;await save(body.destinationId,{live_url:null});}
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
      if(route==='/api/repository/import')return reply(200,await uploads.importGithub(user,body.repository));
      const uploadRoute = route.match(/^\/api\/uploads\/([0-9a-f-]+)\/(prepare|download|doctor)$/i);
      if (uploadRoute) {
        if (uploadRoute[2] === 'doctor') return reply(200, await uploads.doctor(uploadRoute[1], user, body));
        if (uploadRoute[2] === 'prepare') return reply(200, await uploads.prepare(uploadRoute[1], user, body.port, body.allowAI, body.skipScout === true, body.acknowledgeScoutBypass === true));
        const item = await uploads.reviewed(uploadRoute[1], user, body, false);
        const archive = await uploads.archive(item);
        const data = await readFile(archive);
        res.writeHead(200, {'Content-Type':'application/zip','Content-Disposition':'attachment; filename="ebkit-prepared.zip"','Cache-Control':'no-store'});
        return res.end(data);
      }
      if(route==='/api/profiles')return reply(200,{profiles:config.awsAccountId?config.awsProfiles??[]:[]});
      if(route==='/api/trial')return reply(200,trials?await trials.account(user):{approved:true,trialUsed:false,durationMinutes:30});
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
        const { data: existing, error: quotaError } = await admin.database.from('ebkit_web_projects').select('id').eq('user_id', user.id).eq('mode','live').neq('status','destroyed').limit(11);
        if (quotaError) throw new HttpError(503, 'Could not check deployment quota.');
        if (!(config.evaluatorEmails??[]).includes(user.email?.toLowerCase()) && existing.length >= ([...(config.testingEmails??[]),...(config.evaluatorEmails??[])].includes(user.email?.toLowerCase())?10:3)) throw new HttpError(429, 'Destroy an existing live project before creating another.');
        if (running.size) throw new HttpError(409,'Another cloud operation is running.');
        const id = randomUUID(); const application = `ebk-${id.replaceAll('-','').slice(0,16)}`;
        let project = { id, user_id: user.id, name: body.name, repository: repository.repository, region: body.region, port: body.port, mode: 'live', status: 'deploying', stage: 1, replicas: 1, revision: 1, variable_names: Object.keys(body.environment ?? {}), application_name: application, environment_name: `${application}-cluster` };
        let data;
        if(trials){project=await trials.reserve(project,user);data=[project];}
        else{const result=await admin.database.from('ebkit_web_projects').insert([project]).select('*');if(result.error)throw new HttpError(503,'Could not save deployment.');data=result.data;}
        try {
          if (zipItem) {
            body.sourceArchive = await uploads.archive(zipItem);
            zipItem.record.projectId = id;
            await atomicJSON(path.join(zipItem.directory,'record.json'),zipItem.record);
            await atomicJSON(path.join(directoryRoot,`${id}.source.json`),{ uploadId:body.uploadId, reviewHash:body.reviewHash });
          }
          await run('deploy', project, body); launches++;
        } catch (error) { await save(id, { status: 'failed', last_error: error.message,...(project.plan==='free_trial'?{trial_cleanup_state:'pending',trial_cleanup_next_at:new Date().toISOString()}:{}) }); throw error; }
        return reply(202, { project: data[0] });
      }
      const match = route.match(/^\/api\/projects\/([0-9a-f-]+)\/([a-z-]+)$/i);
      if (!match) throw new HttpError(404, 'Endpoint not found.');
      const project = await own(match[1], user);
      const operation=match[2];
      if(operation==='production'){
        const reads=['metrics','cost','leftovers','database-status','database-check','pipeline'];
        const elevated=[...(config.testingEmails??[]),...(config.evaluatorEmails??[])].includes(user.email?.toLowerCase());
        if(!reads.includes(body.action)&&(!elevated||body.confirmation!==project.environment_name))throw new HttpError(403,'This production operation requires designated access and exact environment confirmation.');
        if(project.plan==='free_trial'&&!reads.includes(body.action))throw new HttpError(403,'Persistent databases, alarms, budgets and scaling policies require a deployment without a free-trial lifetime.');
      }
      if(body.profile&&(!(config.awsProfiles??[]).includes(body.profile)||!/^[-A-Za-z0-9_.]+$/.test(body.profile)||!/^\d{12}$/.test(config.awsAccountId??'')))throw new HttpError(403,'Choose a configured operator profile for this AWS account.');
      if(project.plan==='free_trial'&&operation==='options'&&((body.options??[]).some(item=>item?.Namespace?.includes('autoscaling')&&item.Value!=='1')||(body.removeOptions??[]).some(item=>item.includes('autoscaling'))))throw new HttpError(403,'Free trials preserve one replica.');
      const privileged=[...(config.testingEmails??[]),...(config.evaluatorEmails??[])].includes(user.email?.toLowerCase());
      const mutations=['config-save','config-apply','clone','swap','version-delete'].includes(operation)||(operation==='options'&&(body.options||body.removeOptions?.length))||(operation==='config'&&(body.environment||body.unset?.length));
      if(mutations&&body.confirmation!==project.environment_name)throw new HttpError(400,'Confirm the exact source environment name before making this change.');
      if((['clone','swap','version-delete','config-save','config-apply'].includes(operation)||(operation==='options'&&(body.options||body.removeOptions?.length)))&&!privileged)throw new HttpError(403,'This operation requires an operator-designated testing or evaluator account.');
      trials?.guard(project,operation,body,user,true);
      if(operation==='swap'){
        if(running.size||zipReplacing)throw new HttpError(409,'Another cloud operation is running.');
        const destination=await own(body.destinationId,user);
        trials?.guard(destination,'swap',body,user,true);
        if(destination.id===project.id||destination.region!==project.region||destination.application_name!==project.application_name||destination.status!=='ready'||project.status!=='ready'||destination.operation_running)throw new HttpError(400,'Select a different Ready project in the same application and region.');
        body.destinationEnvironment=destination.environment_name;
      }
      if(operation==='clone'){
        if(body.confirmCosts!==true)throw new HttpError(400,'Confirm AWS charges for the new clone.');
        if(running.size||zipReplacing)throw new HttpError(409,'Another cloud operation is running.');
        if(launches>=5)throw new HttpError(429,'Worker launch limit reached.');
        if(project.status!=='ready'||!/^[-a-z0-9]{3,40}$/.test(body.name??''))throw new HttpError(400,'Use a Ready source and a 3?40 character clone name.');
        const {data:existing,error}=await admin.database.from('ebkit_web_projects').select('id').eq('user_id',user.id).eq('mode','live').neq('status','destroyed').limit(11);
        if(error)throw new HttpError(503,'Could not check clone quota.');
        if(!(config.evaluatorEmails??[]).includes(user.email?.toLowerCase())&&existing.length>=10)throw new HttpError(429,'Destroy an existing project before cloning.');
        const id=randomUUID();
        let clone={id,user_id:user.id,name:body.name,repository:project.repository,region:project.region,port:project.port,mode:'live',status:'deploying',stage:1,replicas:project.replicas,revision:1,variable_names:project.variable_names??[],application_name:project.application_name,environment_name:`ebk-${id.replaceAll('-','').slice(0,16)}-cluster`};
        if(trials){clone=await trials.reserve(clone,user);if(clone.plan==='free_trial')clone.replicas=1;}
        else{const result=await admin.database.from('ebkit_web_projects').insert([clone]).select('*');if(result.error||!result.data?.[0])throw new HttpError(503,'Could not save clone.');clone=result.data[0];}
        const originalState=await readJSON(path.join(directoryRoot,`${project.id}.json`)).catch(()=>null);
        if(originalState)await atomicJSON(path.join(directoryRoot,`${id}.json`),{deployments:(originalState.deployments??[]).filter(item=>item.environment_name===project.environment_name)});
        body.sourceEnvironment=project.environment_name;
        try{const result=await run('clone',clone,body);launches++;return reply(202,{...result,project:{...clone,operation_running:true,last_operation:'clone'}});}
        catch(error){await save(clone.id,{status:'failed',trial_cleanup_state:'pending',trial_cleanup_next_at:new Date().toISOString()});throw error;}
      }
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
            const missing=requireEnvironment(inspectEnvironment(inspected.files).filter(item=>!project.variable_names?.includes(item.name)),body.environment);
            if(missing.length)throw new HttpError(400,`Fill in required environment settings: ${missing.join(', ')}.`);
            body.sourceArchive=await uploads.archive(item);
            if(running.size)throw new HttpError(409,'Another cloud operation is running. Wait for it to finish.');
            item.record.projectId=project.id;
            await atomicJSON(path.join(item.directory,'record.json'),item.record);
            await atomicJSON(path.join(directoryRoot,`${project.id}.source.json`),{uploadId:body.uploadId,reviewHash:body.reviewHash});
            const changes={repository:`zip:${item.record.name}`,port:item.record.port,variable_names:[...new Set([...(project.variable_names??[]),...Object.keys(body.environment??{})])]};
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
  const checkTrials=async()=>{if(closing||!trials)return;const task=trials.tick();pollingTasks.add(task);try{await task;}catch{console.error('Trial cleanup scheduler could not reach the database; it will retry.');}finally{pollingTasks.delete(task);}};
  const trialTimer=trials?setInterval(()=>void checkTrials(),dependencies.trialInterval??30000):null;
  if(trials)await checkTrials();
  const checkGithub=async()=>{if(closing||!githubDeploys)return;const task=githubDeploys.tick();pollingTasks.add(task);try{await task;}catch{console.error('GitHub auto-deploy watcher could not finish; it will retry.');}finally{pollingTasks.delete(task);}};
  const githubTimer=githubDeploys?setInterval(()=>void checkGithub(),config.githubPollIntervalMs??300000):null;
  const stopWatching = () => {
    clearInterval(trialTimer);clearInterval(githubTimer);
    closing = true;
    for (const timer of monitors) clearInterval(timer); monitors.clear();
    void Promise.allSettled([...pollingTasks]).then(release);
  };
  server.once('close', stopWatching); server.once('error', stopWatching);
  return server;
}
