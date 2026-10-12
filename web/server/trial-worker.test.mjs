import {afterEach,expect,it,vi} from 'vitest';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createWorker} from './worker.mjs';
const roots=[],servers=[];
afterEach(async()=>{for(const server of servers.splice(0)){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});
async function fixture(){
 const root=await mkdtemp(path.join(tmpdir(),'ebkit-trial-worker-'));roots.push(root);
 const rows=[],claims=[],children=[];let clock=Date.now();
 const admin={database:{from(table){let payload,inserted,filters=[];const records=table==='ebkit_web_trial_claims'?claims:rows;
  const execute=()=>{const matches=records.filter(row=>filters.every(([key,value,negative])=>negative?row[key]!==value:row[key]===value));if(payload)matches.forEach(row=>Object.assign(row,payload));if(inserted)records.push(...inserted);return {data:inserted??matches,error:null};};
  const q={select:()=>q,eq:(key,value)=>{filters.push([key,value,false]);return q;},neq:(key,value)=>{filters.push([key,value,true]);return q;},order:()=>q,limit:async()=>execute(),update:value=>{payload=value;return q;},insert:value=>{inserted=value;return q;},then:(resolve,reject)=>Promise.resolve(execute()).then(resolve,reject)};return q;
 },rpc:vi.fn(async(name,args)=>{
  if(name==='ebkit_reserve_trial'){
   if(claims.some(row=>row.user_id===args.p_project.user_id))return {error:{message:'TRIAL_USED'}};
   if(rows.filter(row=>row.status!=='destroyed'||row.trial_cleanup_state!=='complete').length>=args.p_capacity)return {error:{message:'CAPACITY_FULL'}};
   const project={...args.p_project,plan:'free_trial',trial_cleanup_state:'scheduled',trial_reserved_at:new Date(clock).toISOString(),trial_expires_at:new Date(clock+1800000).toISOString(),trial_cleanup_next_at:new Date(clock+1800000).toISOString()};rows.push(project);claims.push({user_id:project.user_id,project_id:project.id});return {data:project,error:null};
  }
  const row=rows.find(row=>row.id===args.p_id);if(!row.trial_started_at){row.trial_started_at=new Date(clock).toISOString();row.trial_expires_at=new Date(clock+1800000).toISOString();row.trial_cleanup_next_at=row.trial_expires_at;}return {data:{trial_started_at:row.trial_started_at,trial_expires_at:row.trial_expires_at,trial_cleanup_next_at:row.trial_cleanup_next_at},error:null};
 })}};
 const launch=vi.fn(()=>{const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=vi.fn();children.push(child);return child;});
 async function serve(){const server=await createWorker({liveEnabled:true,python:'mock-python',jobRoot:root,allowedEmails:['owner@example.test'],origins:[],globalCapacity:2},{admin,spawn:launch,recover:false,now:()=>clock,trialInterval:20,authenticate:async()=>({id:'owner',email:'owner@example.test'}),fetch:async url=>new Response(JSON.stringify(url.includes('/contents/')?{type:'file',size:200}:{private:false,default_branch:'main'}))});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));servers.push(server);return {server,url:`http://127.0.0.1:${server.address().port}`};}
 const post=(url,route,body={})=>fetch(url+route,{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/json'},body:JSON.stringify(body)});
 return {root,rows,claims,children,launch,admin,serve,post,advance:ms=>clock+=ms};
}
const request={name:'trial-app',repository:'https://github.com/owner/project',region:'us-east-2',port:3000,confirmCosts:true};
it('reserves one trial, starts it at Green, retains expiry on redeploy and blocks a second trial',async()=>{
 const f=await fixture(),{url}=await f.serve();
 const response=await f.post(url,'/api/deploy',request);expect(response.status).toBe(202);const project=(await response.json()).project;expect(project.plan).toBe('free_trial');expect(f.launch.mock.calls[0][1]).toContain('--max-replicas');
 const ready={deployments:[{status:'Ready',health:'Green',url:'https://owned.example.com'}]};await writeFile(path.join(f.root,`${project.id}.json`),JSON.stringify(ready));f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.rows[0].status).toBe('ready'));const deadline=f.rows[0].trial_expires_at;expect(f.rows[0].trial_started_at).toBeTruthy();
 expect((await f.post(url,`/api/projects/${project.id}/scale`,{replicas:2})).status).toBe(403);
 expect((await f.post(url,`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(202);f.children[1].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].operation_running).toBe(false));expect(f.rows[0].trial_expires_at).toBe(deadline);
 expect((await f.post(url,'/api/deploy',request)).status).toBe(403);expect(f.launch).toHaveBeenCalledTimes(2);
 expect(await(await f.post(url,'/api/trial')).json()).toMatchObject({trialUsed:true,approved:true,durationMinutes:30});
 f.advance(1800001);expect((await f.post(url,`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(410);
});
it('resumes overdue destruction on startup and retains the slot until managed cleanup is verified',async()=>{
 const f=await fixture();f.rows.push({id:'22222222-2222-4222-8222-222222222222',user_id:'owner',mode:'live',name:'trial-app',repository:request.repository,region:'us-east-2',port:3000,application_name:'ebk-trial',environment_name:'ebk-trial-cluster',plan:'free_trial',status:'ready',trial_cleanup_state:'scheduled',trial_expires_at:new Date(Date.now()-1000).toISOString(),trial_cleanup_next_at:new Date(Date.now()-1000).toISOString()});
 await f.serve();expect(f.launch).toHaveBeenCalledOnce();expect(f.launch.mock.calls[0][1][2]).toBe('destroy');expect(f.launch.mock.calls[0][1]).toContain('ebk-trial-cluster');
 f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].trial_cleanup_state).toBe('verifying'));expect(f.rows[0].status).toBe('destroyed');
 f.advance(2000);f.rows[0].trial_cleanup_next_at=new Date(Date.now()-1).toISOString();await vi.waitFor(()=>expect(f.launch).toHaveBeenCalledTimes(2));expect(f.launch.mock.calls[1][1]).toContain('--json');
 f.children[1].stdout.write(JSON.stringify({environment:'wrong-project',cleanup_complete:true}));f.children[1].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].operation_running).toBe(false));expect(f.rows[0].trial_cleanup_state).toBe('verifying');
 f.advance(2000);f.rows[0].trial_cleanup_next_at=new Date(Date.now()-1).toISOString();await vi.waitFor(()=>expect(f.launch).toHaveBeenCalledTimes(3));
 f.children[2].stdout.write(JSON.stringify({environment:'ebk-trial-cluster',cleanup_complete:true}));f.children[2].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].trial_cleanup_state).toBe('complete'));
});
it('rejects global capacity before creating a project or starting a cloud command',async()=>{
 const f=await fixture();f.rows.push({id:'a',status:'ready'},{id:'b',status:'destroyed',trial_cleanup_state:'verifying'});const {url}=await f.serve();
 expect((await f.post(url,'/api/deploy',request)).status).toBe(429);expect(f.launch).not.toHaveBeenCalled();expect(f.rows).toHaveLength(2);
});
