import { afterEach, describe, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { createWorker, commandArguments, validateRepository, redact } from './worker.mjs';
const github = vi.fn(async url => new Response(JSON.stringify(url.includes('/contents/') ? { type: 'file', size: 200 } : { private: false, archived: false, default_branch: 'main' }), { status: 200 }));
const servers = [];
afterEach(async () => { github.mockClear(); for (const server of servers.splice(0)) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); } });
async function serve(config = {}, deps = {}) { const server = await createWorker({ trialsEnabled:false,liveEnabled: false, allowedEmails: [], origins: ['http://localhost:5173'], ...config }, { recover: false, ...deps }); await new Promise(resolve => server.listen(0,'127.0.0.1',resolve)); servers.push(server); return `http://127.0.0.1:${server.address().port}`; }
const post = (url, body, headers = {}) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body) });
const project = { id: '11111111-1111-4111-8111-111111111111', user_id: 'owner', mode: 'live', name: 'demo-app', repository: 'https://github.com/owner/project', application_name: 'ebk-test', environment_name: 'ebk-test-cluster', port: 8080, region: 'us-east-2' };
function database(rows) {
  return { database: { from: () => {
    let operation = 'select', payload, filters = [], count = 50;
    const execute = () => {
      const matches = rows.filter(row => filters.every(([key,value,negative]) => negative ? row[key] !== value : row[key] === value));
      if (operation === 'update') matches.forEach(row => Object.assign(row,payload));
      if (operation === 'insert') { rows.push(...payload); return {data:payload,error:null}; }
      return {data:matches.slice(0,count),error:null};
    };
    const builder = { select: () => builder, eq: (key,value) => {filters.push([key,value,false]);return builder;}, neq: (key,value) => {filters.push([key,value,true]);return builder;}, limit: value => {count=value;return Promise.resolve(execute());}, update: value => {operation='update';payload=value;return builder;}, insert: value => {operation='insert';payload=value;return builder;}, then: (resolve,reject) => Promise.resolve(execute()).then(resolve,reject) };
    return builder;
  } } };
}
function fakeProcess() { const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough(); child.kill = vi.fn(); return child; }
describe('real repository validation', () => {
  it('checks metadata and root Dockerfile on the default branch', async () => { const result = await validateRepository(project.repository + '.git', github); expect(result.dockerfile).toBe('Dockerfile'); expect(github.mock.calls[1][0]).toContain('/contents/Dockerfile?ref=main'); });
  it('reports missing Dockerfiles', async () => { await expect(validateRepository(project.repository, async url => url.includes('/contents/') ? new Response('{}',{status:404}) : github(url))).rejects.toThrow('No root Dockerfile'); });
  it.each(['https://evil.com/owner/repo','https://github.com/owner/repo?token=secret','https://github.com/owner/..'])('rejects unsafe source %s', async source => { await expect(validateRepository(source, github)).rejects.toThrow(); expect(github).not.toHaveBeenCalled(); });
});
describe('cloud command adapter', () => {
  it.each(['status','logs','versions','resources','cleanup-status'])('targets the owned environment for %s', operation => { expect(commandArguments(operation, project)).toContain(project.environment_name); });
  it('validates scaling bounds', () => { expect(() => commandArguments('scale',project,{replicas:0})).toThrow(); expect(commandArguments('scale',project,{replicas:3})).toContain('3'); });
  it('requires a real version label for rollback', () => { expect(() => commandArguments('rollback',project,{version:'a; rm -rf'})).toThrow(); expect(commandArguments('rollback',project,{version:'v123'})).toContain('v123'); });
  it('requires exact environment confirmation for destroy', () => { expect(() => commandArguments('destroy',project,{confirmation:'wrong'})).toThrow(); expect(commandArguments('destroy',project,{confirmation:project.environment_name})[2]).toBe('destroy'); });
  it('redacts secret values and AWS keys', () => { expect(redact('password=private AKIA1234567890123456',['private'])).not.toMatch(/private|AKIA/); });
});
describe('authenticated worker API', () => {
  it('serves repository validation without calling AWS', async () => { const spawn = vi.fn(); const url = await serve({}, { spawn, fetch: github }); const response = await post(url+'/api/repository',{repository:project.repository}); expect(response.status).toBe(200); expect(spawn).not.toHaveBeenCalled(); });
  it('fails closed when live mode is disabled', async () => { const spawn = vi.fn(); const url = await serve({}, { spawn }); const response = await post(url+'/api/deploy',{}); expect(response.status).toBe(503); expect(spawn).not.toHaveBeenCalled(); });
  it('requires authentication for cloud operations', async () => { const url = await serve({trialsEnabled:false,liveEnabled:true}, {admin:{},spawn:vi.fn()}); expect((await post(url+'/api/deploy',{})).status).toBe(401); });
  it('rejects users outside the operator allowlist', async () => {
    const spawn = vi.fn(), from = vi.fn();
    const url = await serve({trialsEnabled:false,liveEnabled:true,allowedEmails:['owner@example.test']}, {admin:{database:{from}},authenticate:async()=>({id:'other',email:'other@example.test'}),spawn,fetch:github});
    for (const route of ['/api/deploy', `/api/projects/${project.id}/deploy`]) {
      const response = await post(url+route,{repository:project.repository,name:'demo-app',region:project.region,port:8080,confirmCosts:true}, {Authorization:'Bearer test'});
      expect(response.status).toBe(403);
      expect((await response.json()).error).toBe('This account is not enabled for deployments on this AWS account.');
    }
    expect(spawn).not.toHaveBeenCalled();
    expect(from).not.toHaveBeenCalled();
    expect(github).not.toHaveBeenCalled();
  });
  it('rejects another user\'s project before any AWS action', async () => { const spawn = vi.fn(); const builder = {select:()=>builder,eq:()=>builder,limit:async()=>({data:[],error:null})}; const url = await serve({trialsEnabled:false,liveEnabled:true,allowedEmails:['owner@example.test']},{admin:{database:{from:()=>builder}},authenticate:async()=>({id:'owner',email:'owner@example.test'}),spawn}); expect((await post(url+`/api/projects/${project.id}/destroy`,{confirmation:project.environment_name},{Authorization:'Bearer test'})).status).toBe(404); expect(spawn).not.toHaveBeenCalled(); });
  it('rejects foreign origins before processing cloud requests', async () => { const url = await serve(); expect((await post(url+'/api/deploy',{}, {Origin:'https://evil.com'})).status).toBe(403); });
  it.each(['status','logs','versions','resources','cleanup-status','scale','rollback','destroy'])('runs owned %s using the CLI adapter with no shell', async operation => {
    const rows = [{ ...project, revision: 2, status: 'ready' }], child = fakeProcess(), spawn = vi.fn(() => child);
    const url = await serve({trialsEnabled:false,liveEnabled:true,python:'python',allowedEmails:['owner@example.test']},{admin:database(rows),authenticate:async()=>({id:'owner',email:'owner@example.test'}),spawn});
    const response = await post(url+`/api/projects/${project.id}/${operation}`,{replicas:3,version:'v123',confirmation:project.environment_name},{Authorization:'Bearer test'});
    expect(response.status).toBe(202); expect(spawn.mock.calls[0][2].shell).toBe(false); expect(spawn.mock.calls[0][1]).toContain(project.environment_name);
    expect(spawn.mock.calls[0][2].env.INSFORGE_API_KEY).toBeUndefined(); expect(spawn.mock.calls[0][2].env.PYTHONIOENCODING).toBe('utf-8');
    child.stdout.write('AWS operation output'); child.emit('close',0);
    await vi.waitFor(() => expect(rows[0].operation_running).toBe(false));
    expect(rows[0].operation_log).toContain('AWS operation output');
    if (operation === 'scale') expect(rows[0].replicas).toBe(3);
    if (operation === 'destroy') expect(rows[0].status).toBe('destroyed');
  });
  it('starts deployment, redacts split secret output and records a real failure', async () => {
    const rows = [], child = fakeProcess(), spawn = vi.fn(() => child);
    const url = await serve({trialsEnabled:false,liveEnabled:true,python:'python',allowedEmails:['owner@example.test']},{admin:database(rows),authenticate:async()=>({id:'owner',email:'owner@example.test'}),spawn,fetch:github});
    const response = await post(url+'/api/deploy',{name:'new-app',repository:project.repository,port:8080,region:'us-east-2',environment:{API_KEY:'secret-value'},confirmCosts:true},{Authorization:'Bearer test'});
    expect(response.status).toBe(202); expect(spawn.mock.calls[0][1]).toContain('--env-file');
    child.stdout.write('key=secret-'); child.stdout.write('value'); child.emit('close',1);
    await vi.waitFor(() => expect(rows[0].operation_running).toBe(false));
    expect(rows[0].status).toBe('failed'); expect(rows[0].operation_log).toContain('[REDACTED]'); expect(JSON.stringify(rows)).not.toContain('secret-value');
  });
});

it('refreshes the saved project health and URL from actual structured status', async () => {
  const rows = [{ ...project, status: 'failed', health: 'Red', live_url: null }], child = fakeProcess();
  const url = await serve({trialsEnabled:false,liveEnabled:true,python:'python',allowedEmails:['owner@example.test']},{admin:database(rows),authenticate:async()=>({id:'owner',email:'owner@example.test'}),spawn:()=>child});
  expect((await post(url+`/api/projects/${project.id}/status`,{}, {Authorization:'Bearer test'})).status).toBe(202);
  child.stdout.write(JSON.stringify({environment:project.environment_name,status:'Ready',health:'Green',url:'https://app.example.com'}));child.emit('close',0);
  await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));
  expect(rows[0].status).toBe('ready');expect(rows[0].health).toBe('Green');expect(rows[0].live_url).toBe('https://app.example.com');
});

it('rejects missing detected database settings before any insert or cloud spawn', async()=>{
 const spawn=vi.fn(), from=vi.fn();
 const request=async url=>{
  if(url.includes('/git/trees/'))return new Response(JSON.stringify({tree:[{path:'.env.example',type:'blob',size:30}]}));
  if(url.includes('/contents/.env.example'))return new Response(JSON.stringify({encoding:'base64',content:Buffer.from('POSTGRES_URL=').toString('base64'),size:13}));
  return github(url);
 };
 const url=await serve({trialsEnabled:false,liveEnabled:true,allowedEmails:['owner@example.test']},{admin:{database:{from}},authenticate:async()=>({id:'owner',email:'owner@example.test'}),spawn,fetch:request});
 const response=await post(url+'/api/deploy',{name:'demo',repository:project.repository,region:project.region,port:8080,environment:{},confirmCosts:true},{Authorization:'Bearer test'});
 expect(response.status).toBe(400);expect((await response.json()).error).toContain('POSTGRES_URL');
 expect(spawn).not.toHaveBeenCalled();expect(from).not.toHaveBeenCalled();
});

it('rejects signed-out or expired sessions on every cloud endpoint before any AWS action',async()=>{
 const spawn=vi.fn(),from=vi.fn(),inspectZip=vi.fn(),githubRequest=vi.fn();
 const url=await serve({trialsEnabled:false,liveEnabled:true,allowedEmails:['owner@example.test']},{admin:{database:{from}},spawn,inspectZip,fetch:githubRequest,authenticate:async()=>{throw new Error('expired session');}});
 const routes=['/api/deploy','/api/trial','/api/uploads',`/api/uploads/${project.id}/prepare`,`/api/uploads/${project.id}/download`,...['deploy','rollback','scale','destroy','status','logs','versions','resources','cleanup-status'].map(op=>`/api/projects/${project.id}/${op}`)];
 for(const route of routes)for(const headers of [{},{Authorization:'Bearer expired-session'},{Authorization:'Bearer null'}]){
  const response=await post(url+route,{...project,confirmCosts:true,confirmation:project.environment_name},headers);expect(response.status).toBe(401);
 }
 expect(spawn).not.toHaveBeenCalled();expect(from).not.toHaveBeenCalled();expect(inspectZip).not.toHaveBeenCalled();expect(githubRequest).not.toHaveBeenCalled();
});
