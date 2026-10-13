import {afterEach,expect,it,vi} from 'vitest';
import {mkdtemp,rm,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createWorker,commandArguments} from './worker.mjs';
const resources=[];
afterEach(async()=>{for(const {server,root} of resources.splice(0)){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));await rm(root,{recursive:true,force:true});}});
const id='11111111-1111-4111-8111-111111111111',otherId='22222222-2222-4222-8222-222222222222';
async function fixture(config={}){
 const root=await mkdtemp(path.join(tmpdir(),'ebkit-controls-'));
 const project={id,user_id:'owner',name:'app',mode:'live',status:'ready',repository:'https://github.com/a/b',application_name:'app',environment_name:'source-env',region:'us-east-2',port:8080,replicas:1,revision:1,variable_names:['KEEP'],live_url:'https://source.example'};
 const rows=[project,{...project,id:otherId,name:'green',environment_name:'green-env',live_url:'https://green.example'}];
 const admin={database:{from(){let operation='select',payload,filters=[];const execute=()=>{const matches=rows.filter(row=>filters.every(([key,value,neg])=>neg?row[key]!==value:row[key]===value));if(operation==='update')matches.forEach(row=>Object.assign(row,payload));if(operation==='insert'){rows.push(...payload);return {data:payload};}return {data:matches};};const q={select:()=>q,eq:(key,value)=>{filters.push([key,value,false]);return q;},neq:(key,value)=>{filters.push([key,value,true]);return q;},order:()=>q,limit:async()=>execute(),update:value=>{operation='update';payload=value;return q;},insert:value=>{operation='insert';payload=value;return q;},then:(resolve,reject)=>Promise.resolve(execute()).then(resolve,reject)};return q;}}};
 const children=[],spawn=vi.fn(()=>{const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=vi.fn();children.push(child);return child;});
 const server=await createWorker({trialsEnabled:false,githubAutoDeploy:false,liveEnabled:true,python:'mock',jobRoot:root,allowedEmails:['owner@example.test'],testingEmails:['owner@example.test'],origins:[],...config},{admin,spawn,recover:false,authenticate:async()=>({id:'owner',email:'owner@example.test'})});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));resources.push({server,root});
 const url=`http://127.0.0.1:${server.address().port}`;
 const post=(operation,body={},projectId=id)=>fetch(`${url}/api/projects/${projectId}/${operation}`,{method:'POST',headers:{Authorization:'Bearer ok','Content-Type':'application/json'},body:JSON.stringify(body)});
 return {root,rows,project,post,spawn,children,url};
}
it.each(['health','events','options','config'])('runs owned %s through the CLI adapter',async operation=>{
 const f=await fixture();expect((await f.post(operation)).status).toBe(202);
 expect(f.spawn.mock.calls[0][1]).toContain(operation);expect(f.spawn.mock.calls[0][1]).toContain('source-env');expect(f.spawn.mock.calls[0][2].shell).toBe(false);
 f.children[0].stdout.write('Read-only result');f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.project.operation_running).toBe(false));
 expect(f.project.operation_log).toContain('Read-only result');
});
it('updates variable names and redacts values without saving them to project metadata',async()=>{
 const f=await fixture();const body={confirmation:'source-env',environment:{NEW:'secret-setting'},unset:['KEEP']};
 expect((await f.post('config',body)).status).toBe(202);
 const args=f.spawn.mock.calls[0][1];expect(args).toContain('--env-file');expect(args).not.toContain('secret-setting');
 f.children[0].stdout.write('secret-setting');f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.project.operation_running).toBe(false));
 expect(f.project.variable_names).toEqual(['NEW']);expect(JSON.stringify(f.rows)).not.toContain('secret-setting');
});
it('writes full options to a protected file and redacts their values',async()=>{
 const f=await fixture();const options=[{Namespace:'aws:elasticbeanstalk:eks:environment:startup-probe',OptionName:'initial-delay-seconds',Value:'private-option-value'}];
 expect((await f.post('options',{confirmation:'source-env',options})).status).toBe(202);
 const args=f.spawn.mock.calls[0][1],file=args[args.indexOf('--file')+1];expect(JSON.parse(await readFile(file,'utf8'))).toEqual(options);
 expect(args).not.toContain('private-option-value');f.children[0].stdout.write('private-option-value');f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.project.operation_running).toBe(false));expect(f.project.operation_log).toContain('[REDACTED]');
});
it('checks exact confirmations and requires designated accounts for advanced mutations',async()=>{
 const f=await fixture({testingEmails:[]});
 expect((await f.post('config',{environment:{NEW:'value'}})).status).toBe(400);
 expect((await f.post('version-delete',{confirmation:'source-env',version:'old'})).status).toBe(403);
 expect((await f.post('options',{confirmation:'source-env',options:[]})).status).toBe(403);
 expect((await f.post('clone',{confirmation:'source-env',name:'clone-app',confirmCosts:true})).status).toBe(403);
 expect(f.spawn).not.toHaveBeenCalled();
});
it('blocks unknown profiles and forwards only approved same-account profiles',async()=>{
 const f=await fixture({awsProfiles:['operator'],awsAccountId:'123456789012'});
 expect((await f.post('health',{profile:'foreign'})).status).toBe(403);
 expect((await f.post('health',{profile:'operator'})).status).toBe(202);
 expect(f.spawn.mock.calls[0][1].slice(0,5)).toEqual(['-m','ebkit.cli','--profile','operator','health']);
 expect(f.spawn.mock.calls[0][2].env.EBKIT_EXPECTED_AWS_ACCOUNT).toBe('123456789012');
});
it('requires ownership of both swap projects and clears their stale URLs',async()=>{
 const f=await fixture();f.rows[1].user_id='other';
 expect((await f.post('swap',{confirmation:'source-env',destinationId:otherId})).status).toBe(404);
 f.rows[1].user_id='owner';expect((await f.post('swap',{confirmation:'source-env',destinationId:otherId})).status).toBe(202);
 expect(f.spawn.mock.calls[0][1]).toContain('green-env');f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.project.operation_running).toBe(false));expect(f.rows.map(row=>row.live_url)).toEqual([null,null]);
});
it('creates a tracked clone in the same application with a distinct environment',async()=>{
 const f=await fixture();expect((await f.post('clone',{confirmation:'source-env',name:'clone-app',confirmCosts:true})).status).toBe(202);
 const clone=f.rows[2];expect(clone.application_name).toBe('app');expect(clone.environment_name).not.toBe('source-env');
 expect(f.spawn.mock.calls[0][1]).toContain('source-env');expect(f.spawn.mock.calls[0][1]).toContain(clone.environment_name);
 await writeFile(path.join(f.root,`${clone.id}.json`),JSON.stringify({deployments:[{environment_name:'source-env',status:'Ready',health:'Green',url:'https://wrong.example'},{environment_name:clone.environment_name,status:'Ready',health:'Green',url:'https://clone.example'}]}));
 f.children[0].emit('close',0);await vi.waitFor(()=>expect(clone.operation_running).toBe(false));expect(clone.live_url).toBe('https://clone.example');
});
it('blocks free-trial options that increase capacity',async()=>{
 const f=await fixture();f.project.plan='free_trial';
 expect((await f.post('options',{confirmation:'source-env',options:[{Namespace:'aws:elasticbeanstalk:eks:environment:autoscaling',OptionName:'max-replica',Value:'2'}]})).status).toBe(403);
 expect(f.spawn).not.toHaveBeenCalled();
});
it.each(['config-save','config-apply','version-delete'])('validates and adapts %s',async operation=>{
 const f=await fixture();expect((await f.post(operation,{confirmation:'source-env',templateName:'saved',version:'old'})).status).toBe(202);
 const args=f.spawn.mock.calls[0][1];expect(args).toContain(operation);expect(args).toContain('--yes');
 if(operation.startsWith('config-'))expect(args).toContain(`ebkit-${id}-saved`);
});
it('rejects malformed options and variable removals',()=>{
 const project={application_name:'app',environment_name:'env',region:'us-east-2'};
 expect(()=>commandArguments('options',project,{options:'bad'})).toThrow('Invalid');
 expect(()=>commandArguments('config',project,{unset:['PORT']})).toThrow('Invalid');
});
