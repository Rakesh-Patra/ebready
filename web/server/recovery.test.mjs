import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, writeFile, readFile, rm, access, rename } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createWorker, redact } from './worker.mjs';
import { atomicJSON, readJSON, safeOutput, lockJobs } from './jobs.mjs';

const project = { id: '22222222-2222-4222-8222-222222222222', user_id: 'owner', name: 'recovery-app', repository: 'https://github.com/owner/project', mode: 'live', application_name: 'ebk-recovery', environment_name: 'ebk-recovery-cluster', port: 8080, region: 'us-east-2', revision: 1, status: 'ready', operation_running: false };
const directories = [], servers = [];
it('retries temporary journal replacement locks without losing progress', async () => {
  const root = await directory(), file = path.join(root, 'journal.json');
  await atomicJSON(file, { state: 'old' });
  const replace = vi.fn().mockRejectedValueOnce(Object.assign(new Error('locked'), { code: 'EPERM' })).mockRejectedValueOnce(Object.assign(new Error('busy'), { code: 'EBUSY' })).mockImplementation(rename);
  await atomicJSON(file, { state: 'running' }, replace);
  expect(replace).toHaveBeenCalledTimes(3);
  expect(await readJSON(file)).toEqual({ state: 'running' });
});
it('preserves the last journal on a permanent filesystem failure', async () => {
  const root = await directory(), file = path.join(root, 'journal.json');
  await atomicJSON(file, { state: 'old' });
  const replace = vi.fn().mockRejectedValue(Object.assign(new Error('disk full'), { code: 'ENOSPC' }));
  await expect(atomicJSON(file, { state: 'running' }, replace)).rejects.toMatchObject({ code: 'ENOSPC' });
  expect(replace).toHaveBeenCalledTimes(1);
  expect(await readJSON(file)).toEqual({ state: 'old' });
});
async function directory() { const result = await mkdtemp(path.join(tmpdir(), 'ebkit-recovery-')); directories.push(result); return result; }
function database(rows, fail = () => false) {
  return { database: { from() {
    let payload, filters = [], count = 1001;
    const execute = () => {
      if (fail(!!payload)) return { data: null, error: { message: 'Offline' } };
      const matches = rows.filter(row => filters.every(([key,value]) => row[key] === value));
      if (payload) matches.forEach(row => Object.assign(row, payload));
      return { data: matches.slice(0,count), error: null };
    };
    const query = { select: () => query, eq: (key,value) => { filters.push([key,value]); return query; }, limit: value => {count=value;return Promise.resolve(execute());}, update: value => {payload=value;return query;}, then: (resolve,reject) => Promise.resolve(execute()).then(resolve,reject) };
    return query;
  } } };
}
const config = root => ({ trialsEnabled:false,liveEnabled: true, python: process.execPath, allowedEmails: ['owner@example.test'], origins: [], jobRoot: root });
async function serve(root, admin, deps = {}) {
  const server = await createWorker({...config(root),...deps.config}, { admin, authenticate: async()=>({id:'owner',email:'owner@example.test'}), fetch: async url => new Response(JSON.stringify(url.includes('/contents/') ? { type:'file',size:200 } : {private:false,archived:false,default_branch:'main'})), pollInterval: 30, ...deps });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve)); servers.push(server);
  return { server, url: `http://127.0.0.1:${server.address().port}` };
}
async function close(server, root) {
  server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  servers.splice(servers.indexOf(server),1);
  await vi.waitFor(async()=>{ await expect(access(path.join(root,'worker.lock'))).rejects.toThrow(); });
}
const post = (url, body = {}) => fetch(url, {method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/json'},body:JSON.stringify(body)});
async function journal(root, overrides = {}) {
  const folder = path.join(root,'operation-test');
  const file = path.join(root,`journal-${project.id}.json`);
  await atomicJSON(file, { id:'job-one', project:{...project}, operation:'deploy', body:{}, directory:folder, state:'running', started:Date.now()-60000, deadline:Date.now()+60000, applied:false, output:'Previous output', ...overrides });
  return file;
}
afterEach(async()=> {
  for (const root of directories) {
    await writeFile(path.join(root,'finish'),'yes');
    const file=path.join(root,`journal-${project.id}.json`);
    const saved=await readJSON(file).catch(()=>null);
    if (saved?.runnerPid) await vi.waitFor(async()=>expect((await readJSON(file)).state).toBe('finished'),{timeout:6000});
  }
  for (const server of servers.splice(0)) {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  for (const root of directories.splice(0)) await rm(root,{recursive:true,force:true,maxRetries:5,retryDelay:100});
});

it('reattaches across a backend restart without a second CLI launch or losing secrets/logs', {timeout:12000}, async()=> {
  const root = await directory(), rows = [{...project}], admin = database(rows);
  const fixture = path.join(root,'fake-cli.mjs');
  // Node fixture substitutes for EBKit: no Python, AWS SDK or AWS credentials required.
  await writeFile(fixture, `import {readFile,writeFile,access} from 'node:fs/promises';
    const envFile=process.argv[process.argv.indexOf('--env-file')+1];
    const text=await readFile(envFile,'utf8');const secret=text.slice(text.indexOf('="')+2,-1);
    const root=${JSON.stringify(root)};
    await writeFile(root+'/started','yes');
    process.stdout.write('API_KEY='+secret.slice(0,7));
    await new Promise(r=>setTimeout(r,1300));process.stdout.write(secret.slice(7)+'\\n');
    while(true){try{await access(root+'/finish');break;}catch{await new Promise(r=>setTimeout(r,20));}}
    await writeFile(process.env.EBKIT_STATE_FILE,JSON.stringify({deployments:[{status:'Ready',health:'Green',url:'https://recovered.example.com'}]}));
    process.stdout.write('Deployment complete\\n');`);
  const launch = vi.fn((...args)=>spawn(...args));
  const first = await serve(root,admin,{spawn:launch,durableJobs:true,commandArguments:()=>[fixture]});
  const response = await post(first.url+`/api/projects/${project.id}/deploy`,{confirmCosts:true,environment:{API_KEY:'test-private-secret'}});
  expect(response.status).toBe(202);
  await vi.waitFor(()=>access(path.join(root,'started')));
  await close(first.server,root);
  const second = await serve(root,admin,{spawn:launch,durableJobs:true});
  expect(rows[0].operation_running).toBe(true);
  expect((await post(second.url+`/api/projects/${project.id}/scale`,{replicas:2})).status).toBe(409);
  await writeFile(path.join(root,'finish'),'yes');
  await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false),{timeout:6000});
  expect(launch).toHaveBeenCalledTimes(1);
  expect(rows[0]).toMatchObject({status:'ready',health:'Green',live_url:'https://recovered.example.com',revision:2});
  expect(rows[0].operation_log).toContain('Deployment complete');
  expect(rows[0].operation_log).toContain('[REDACTED]');
  expect(JSON.stringify(rows)).not.toContain('test-private');
  const savedFile=path.join(root,`journal-${project.id}.json`);
  await vi.waitFor(async()=>expect((await readJSON(savedFile)).applied).toBe(true));
  const saved=await readJSON(savedFile);expect(JSON.stringify(saved)).not.toContain('test-private');
  await vi.waitFor(async()=>{await expect(access(saved.directory)).rejects.toThrow();});
});

it('replays a completed result after a database outage, exactly once, without spawning AWS', async()=> {
  const root=await directory(),rows=[{...project,operation_running:true}],launch=vi.fn();
  await journal(root,{state:'finished',code:0});
  await atomicJSON(path.join(root,`${project.id}.json`),{deployments:[{status:'Ready',health:'Green',url:'https://app.example.com'}]});
  let offline=true;
  const worker=await serve(root,database(rows,writing=>writing&&offline),{spawn:launch,durableJobs:true});
  expect(rows[0].operation_running).toBe(true);offline=false;
  await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));
  expect(rows[0].revision).toBe(2);
  await close(worker.server,root);
  await serve(root,database(rows),{spawn:launch,durableJobs:true});
  expect(rows[0].revision).toBe(2);expect(launch).not.toHaveBeenCalled();
});

it('clears a dead runner with a useful interrupted error instead of repeating a deployment', async()=> {
  const root=await directory(),rows=[{...project,status:'deploying',operation_running:true}],launch=vi.fn();
  await journal(root);
  await serve(root,database(rows),{spawn:launch,durableJobs:true});
  expect(rows[0].operation_running).toBe(false);expect(rows[0].status).toBe('failed');
  expect(rows[0].last_error).toContain('interrupted');expect(rows[0].operation_log).toContain('Previous output');
  expect(launch).not.toHaveBeenCalled();
});

it('keeps an orphaned but still alive CLI locked, preventing a duplicate deployment', async()=> {
  const root=await directory(),rows=[{...project,operation_running:true}],launch=vi.fn();
  await journal(root,{childPid:process.pid});
  const worker=await serve(root,database(rows),{spawn:launch,durableJobs:true});
  expect((await post(worker.url+`/api/projects/${project.id}/destroy`,{confirmation:project.environment_name})).status).toBe(409);
  expect(rows[0].operation_running).toBe(true);expect(launch).not.toHaveBeenCalled();
});

it('recovers legacy running flags when no journal remains, without creating resources', async()=> {
  const root=await directory(),rows=[{...project,status:'destroying',operation_running:true}],launch=vi.fn();
  await serve(root,database(rows),{spawn:launch,durableJobs:true});
  expect(rows[0]).toMatchObject({status:'failed',operation_running:false});
  expect(rows[0].last_error).toContain('Check Status');expect(launch).not.toHaveBeenCalled();
});

it('fails closed when startup cannot check the database', async()=> {
  const root=await directory(),launch=vi.fn();
  await expect(serve(root,database([],()=>true),{spawn:launch,durableJobs:true})).rejects.toThrow('Could not check interrupted');
  expect(launch).not.toHaveBeenCalled();await expect(access(path.join(root,'worker.lock'))).rejects.toThrow();
});

it('blocks a second worker using the same job directory', async()=> {
  const root=await directory(),release=await lockJobs(root);
  await expect(lockJobs(root)).rejects.toThrow('Another worker');await release();
  const releaseAgain=await lockJobs(root);await releaseAgain();
});

it('redacts secrets even when a persistence flush splits a secret across chunks',()=> {
  expect(safeOutput('key=test-private', ['test-private-secret'],redact)).toBe('key=[REDACTED]');
  expect(safeOutput('key=test-private-secret\n', ['test-private-secret'],redact)).toBe('key=[REDACTED]\n');
});

it('survives an abrupt backend process termination and adopts the detached job on restart', {timeout:12000}, async()=> {
  const root=await directory(), store=path.join(root,'rows.json'), workerFile=path.join(root,'backend.mjs'), cli=path.join(root,'cli.mjs');
  await writeFile(store,JSON.stringify([{...project}]));
  await writeFile(cli, `import {access,writeFile} from 'node:fs/promises';
    const root=${JSON.stringify(root)};await writeFile(root+'/started','yes');
    while(true){try{await access(root+'/finish');break;}catch{await new Promise(r=>setTimeout(r,20));}}
    await writeFile(process.env.EBKIT_STATE_FILE,JSON.stringify({deployments:[{status:'Ready',health:'Green',url:'https://restart.example.com'}]}));
    console.log('Recovered after process termination');`);
  await writeFile(workerFile, `import {readFileSync,writeFileSync,appendFileSync} from 'node:fs';
    import {spawn} from 'node:child_process';
    import {createWorker} from ${JSON.stringify(new URL('./worker.mjs',import.meta.url).href)};
    const root=${JSON.stringify(root)},store=${JSON.stringify(store)},rows=JSON.parse(readFileSync(store,'utf8'));
    const admin=(${database.toString()})(rows);
    const from=admin.database.from;admin.database.from=()=>{const query=from();const then=query.then;
      query.then=(resolve,reject)=>then(value=>{writeFileSync(store,JSON.stringify(rows));resolve(value);},reject);return query;};
    const server=await createWorker(${JSON.stringify(config(root))},{admin,durableJobs:true,pollInterval:30,
      spawn:(...args)=>{appendFileSync(root+'/launches','launch\\n');return spawn(...args);},
      commandArguments:()=>[${JSON.stringify(cli)}],authenticate:async()=>({id:'owner',email:'owner@example.test'}),
      fetch:async url=>new Response(JSON.stringify(url.includes('/contents/')?{type:'file',size:200}:{private:false,archived:false,default_branch:'main'}))});
    server.listen(0,'127.0.0.1',()=>console.log(server.address().port));`);
  const processes=[];
  async function start() {
    const child=spawn(process.execPath,[workerFile],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,INSFORGE_API_KEY:''}});processes.push(child);
    const [chunk]=await once(child.stdout,'data');
    return {child,url:`http://127.0.0.1:${Number(chunk.toString().trim())}`};
  }
  try {
    const first=await start();expect((await post(first.url+`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(202);
    await vi.waitFor(()=>access(path.join(root,'started')));
    const exited=once(first.child,'close');first.child.kill();await exited;
    const second=await start();
    expect((await post(second.url+`/api/projects/${project.id}/scale`,{replicas:2})).status).toBe(409);
    await writeFile(path.join(root,'finish'),'yes');
    await vi.waitFor(async()=>expect(JSON.parse(await readFile(store,'utf8'))[0]).toMatchObject({operation_running:false,status:'ready',live_url:'https://restart.example.com'}));
    expect((await readFile(path.join(root,'launches'),'utf8')).trim().split('\n')).toHaveLength(1);
  } finally {
    await writeFile(path.join(root,'finish'),'yes');
    for(const child of processes) if(child.exitCode===null&&child.signalCode===null){const exited=once(child,'close');child.kill();await exited;}
  }
});

it('redacts full-option values across a detached job and worker restart', {timeout:12000}, async()=>{
 const root=await directory(),rows=[{...project}],admin=database(rows),fixture=path.join(root,'options-cli.mjs');
 await writeFile(fixture,`import {readFile,writeFile,access} from 'node:fs/promises';
  const file=process.argv[process.argv.indexOf('--file')+1],secret=JSON.parse(await readFile(file,'utf8'))[0].Value;
  await writeFile(${JSON.stringify(root)}+'/started','yes');process.stdout.write(secret.slice(0,7));
  while(true){try{await access(${JSON.stringify(root)}+'/finish');break;}catch{await new Promise(r=>setTimeout(r,20));}}
  process.stdout.write(secret.slice(7));`);
 const launch=vi.fn((...args)=>spawn(...args));
 const first=await serve(root,admin,{spawn:launch,durableJobs:true,commandArguments:()=>[fixture],config:{testingEmails:['owner@example.test']}});
 const response=await post(first.url+`/api/projects/${project.id}/options`,{confirmation:project.environment_name,options:[{Namespace:'namespace',OptionName:'name',Value:'private-options-secret'}]});
 expect(response.status).toBe(202);await vi.waitFor(()=>access(path.join(root,'started')));
 await close(first.server,root);await serve(root,admin,{spawn:launch,durableJobs:true});
 await writeFile(path.join(root,'finish'),'yes');await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false),{timeout:6000});
 expect(launch).toHaveBeenCalledOnce();expect(rows[0].operation_log).toContain('[REDACTED]');
 expect(JSON.stringify(rows)).not.toContain('private-options-secret');
 const saved=await readJSON(path.join(root,`journal-${project.id}.json`));expect(JSON.stringify(saved)).not.toContain('private-options-secret');
});

it('replays variable-name updates from a finished config journal without retaining values',async()=>{
 const root=await directory(),rows=[{...project,variable_names:['OLD'],operation_running:true}];
 await journal(root,{operation:'config',body:{environmentNames:['NEW'],unset:['OLD']},state:'finished',code:0});
 const launch=vi.fn();await serve(root,database(rows),{spawn:launch,durableJobs:true});
 await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));expect(rows[0].variable_names).toEqual(['NEW']);expect(launch).not.toHaveBeenCalled();
});
