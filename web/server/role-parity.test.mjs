import {afterEach,expect,it,vi} from 'vitest';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';
import {createWorker} from './worker.mjs';

const fixtures=[];
const identities=[
 {id:'evaluator',email:'evaluator@example.invalid',plan:null},
 {id:'tester',email:'tester@example.invalid',plan:'free_trial'},
 {id:'tester',email:'tester@example.invalid',plan:'paid_test'},
];
afterEach(async()=>{
 for(const {server,root,children} of fixtures.splice(0)){
  for(const child of children)child.emit('close',0);
  server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
  if(!path.relative(process.cwd(),root).startsWith('.test-tmp-role-'))throw new Error('Unexpected test directory');
  await rm(root,{recursive:true,force:true});
 }
});
async function setup(hosted,identity){
 const root=await mkdtemp(path.join(process.cwd(),'.test-tmp-role-'));
 const row={id:'11111111-1111-4111-8111-111111111111',user_id:identity.id,mode:'live',name:'owned',repository:'https://github.com/example/app',region:'us-east-2',port:80,status:'ready',application_name:'ebk-1111111111111111',environment_name:'ebk-1111111111111111-cluster',plan:identity.plan,trial_expires_at:identity.plan?new Date(Date.now()+3600000).toISOString():null,trial_cleanup_next_at:new Date(Date.now()+3600000).toISOString(),trial_cleanup_state:'scheduled'};
 const children=[];
 const spawn=vi.fn(()=>{const child=new EventEmitter();for(const key of ['stdin','stdout','stderr'])child[key]=new PassThrough();child.kill=vi.fn();children.push(child);return child;});
 const admin={database:{from(table){let filters=[],payload;
  const execute=()=>{const rows=table==='ebkit_web_projects'&&filters.every(([key,value,negative])=>negative?row[key]!==value:row[key]===value)?[row]:[];if(payload)rows.forEach(item=>Object.assign(item,payload));return {data:rows,error:null};};
  const q={select:()=>q,eq:(key,value)=>{filters.push([key,value,false]);return q;},neq:(key,value)=>{filters.push([key,value,true]);return q;},order:()=>q,limit:async()=>execute(),update:value=>{payload=value;return q;},then:(resolve,reject)=>Promise.resolve(execute()).then(resolve,reject)};return q;
 }}};
 const user={...identity,emailVerified:true};
 const server=await createWorker({...(hosted?{hostedRegion:'us-east-2'}:{}),liveEnabled:true,trialsEnabled:true,githubAutoDeploy:false,jobRoot:root,python:'python',origins:[],allowedEmails:identities.map(i=>i.email),testingEmails:identities.map(i=>i.email),evaluatorEmails:[identities[0].email],paidTestEmails:[identities[1].email]},{admin,spawn,recover:false,authenticate:async()=>user});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));fixtures.push({server,root,children});
 const post=async(operation,body={})=>fetch(`http://127.0.0.1:${server.address().port}/api/projects/${row.id}/${operation}`,{method:'POST',headers:{Authorization:'Bearer synthetic','Content-Type':'application/json'},body:JSON.stringify(body)});
 return {row,user,spawn,children,post};
}
const operations=['status','logs','versions','resources','events','health','options','config','rollback','scale','version-delete','destroy'];
for(const hosted of [false,true])for(const identity of identities){
 it.each(operations)(`${hosted?'hosted':'local'} ${identity.id}/${identity.plan??'manual'} permits owned %s`,async operation=>{
  const f=await setup(hosted,identity);
  const response=await f.post(operation,{replicas:1,version:'v1',confirmation:f.row.environment_name});
  expect(response.status,await response.text()).toBe(202);
  expect(f.spawn).toHaveBeenCalledOnce();
  expect(f.spawn.mock.calls[0][1]).toContain(f.row.environment_name);
  expect(f.spawn.mock.calls[0][2].shell).toBe(false);
  f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.row.operation_running).toBe(false));
 });
 it(`${hosted?'hosted':'local'} ${identity.id}/${identity.plan??'manual'} preserves scaling and ownership restrictions`,async()=>{
  const f=await setup(hosted,identity);
  const response=await f.post('scale',{replicas:3});
  expect(response.status).toBe(identity.plan?403:202);
  if(!identity.plan){f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.row.operation_running).toBe(false));}
  const calls=f.spawn.mock.calls.length;f.row.user_id='different-owner';
  expect((await f.post('destroy',{confirmation:f.row.environment_name})).status).toBe(404);
  expect(f.spawn).toHaveBeenCalledTimes(calls);
 });
}
