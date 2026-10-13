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
   const paidTest=args.p_project.operator_paid_test===true;
   if(!paidTest&&claims.some(row=>row.user_id===args.p_project.user_id))return {error:{message:'TRIAL_USED'}};
   if(rows.filter(row=>row.status!=='destroyed'||row.trial_cleanup_state!=='complete').length>=args.p_capacity)return {error:{message:'CAPACITY_FULL'}};
   const project={...args.p_project,plan:paidTest?'paid_test':'free_trial',trial_cleanup_state:'scheduled',trial_reserved_at:new Date(clock).toISOString(),trial_expires_at:new Date(clock+1800000).toISOString(),trial_cleanup_next_at:new Date(clock+1800000).toISOString()};rows.push(project);if(!paidTest)claims.push({user_id:project.user_id,project_id:project.id});return {data:project,error:null};
  }
  const row=rows.find(row=>row.id===args.p_id);if(!row.trial_started_at){row.trial_started_at=new Date(clock).toISOString();row.trial_expires_at=new Date(clock+(row.plan==='paid_test'?7200000:1800000)).toISOString();row.trial_cleanup_next_at=row.trial_expires_at;}return {data:{trial_started_at:row.trial_started_at,trial_expires_at:row.trial_expires_at,trial_cleanup_next_at:row.trial_cleanup_next_at},error:null};
 })}};
 const launch=vi.fn(()=>{const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=vi.fn();children.push(child);return child;});
 async function serve(extra={}){const server=await createWorker({liveEnabled:true,python:'mock-python',jobRoot:root,allowedEmails:['owner@example.test'],paidTestEmails:['owner@example.test'],origins:[],globalCapacity:2,...extra.config},{admin,spawn:launch,recover:false,now:()=>clock,trialInterval:20,authenticate:async()=>({id:'owner',email:'owner@example.test'}),fetch:async url=>new Response(JSON.stringify(url.includes('/contents/')?{type:'file',size:200}:{private:false,default_branch:'main'})),...extra.dependencies});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));servers.push(server);return {server,url:`http://127.0.0.1:${server.address().port}`};}
 const post=(url,route,body={})=>fetch(url+route,{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/json'},body:JSON.stringify(body)});
 return {root,rows,claims,children,launch,admin,serve,post,advance:ms=>clock+=ms};
}
const request={name:'trial-app',repository:'https://github.com/owner/project',region:'us-east-2',port:3000,confirmCosts:true};
it('reserves one trial, starts it at Green and allows updates within the original deadline',async()=>{
 const f=await fixture(),{url}=await f.serve();
 const response=await f.post(url,'/api/deploy',request);expect(response.status).toBe(202);const project=(await response.json()).project;expect(project.plan).toBe('free_trial');expect(f.launch.mock.calls[0][1]).toContain('--max-replicas');
 const ready={deployments:[{status:'Ready',health:'Green',url:'https://owned.example.com'}]};await writeFile(path.join(f.root,`${project.id}.json`),JSON.stringify(ready));f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.rows[0].status).toBe('ready'));const deadline=f.rows[0].trial_expires_at;expect(f.rows[0].trial_started_at).toBeTruthy();
 expect((await f.post(url,`/api/projects/${project.id}/scale`,{replicas:2})).status).toBe(403);
 expect((await f.post(url,`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(202);
 expect((await f.post(url,`/api/projects/${project.id}/rollback`,{version:'v1'})).status).toBe(403);
 await writeFile(path.join(f.root,`${project.id}.json`),JSON.stringify(ready));f.children[1].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].operation_running).toBe(false));
 expect(f.rows[0].trial_expires_at).toBe(deadline);
 expect((await f.post(url,'/api/deploy',request)).status).toBe(403);expect(f.launch).toHaveBeenCalledTimes(2);
 expect(await(await f.post(url,'/api/trial')).json()).toMatchObject({trialUsed:true,approved:true,durationMinutes:30});
 f.advance(1800001);expect((await f.post(url,`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(410);
});
it('starts the allowlisted test pass at Ready/Green for exactly two hours without consuming the free trial',async()=>{
 const f=await fixture(),{url}=await f.serve();
 expect(await(await f.post(url,'/api/trial')).json()).toMatchObject({paidTestAvailable:true,paidTestDurationMinutes:120});
 const response=await f.post(url,'/api/deploy',{...request,testPaidPass:true});expect(response.status).toBe(202);
 const project=(await response.json()).project;expect(project.plan).toBe('paid_test');expect(f.claims).toHaveLength(0);expect(f.launch.mock.calls[0][1]).toContain('--max-replicas');
 const ready={deployments:[{status:'Ready',health:'Green',url:'https://owned.example.com'}]};
 await writeFile(path.join(f.root,`${project.id}.json`),JSON.stringify(ready));f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.rows[0].status).toBe('ready'));
 expect(Date.parse(f.rows[0].trial_expires_at)-Date.parse(f.rows[0].trial_started_at)).toBe(7200000);
 expect((await f.post(url,`/api/projects/${project.id}/scale`,{replicas:2})).status).toBe(403);
 f.advance(7200001);
 expect((await f.post(url,`/api/projects/${project.id}/deploy`,{confirmCosts:true})).status).toBe(410);
});
it('rejects a forged test-pass request for an email that is not allowlisted',async()=>{
 const f=await fixture(),{url}=await f.serve({config:{paidTestEmails:[]}});
 expect((await f.post(url,'/api/deploy',{...request,testPaidPass:true})).status).toBe(403);
 expect(f.admin.database.rpc).not.toHaveBeenCalled();expect(f.launch).not.toHaveBeenCalled();
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

it('retains a busy environment as pending cleanup rather than an operation failure',async()=>{
 const f=await fixture();f.rows.push({id:'33333333-3333-4333-8333-333333333333',user_id:'owner',mode:'live',name:'trial-app',repository:request.repository,region:'us-east-2',port:3000,application_name:'ebk-pending',environment_name:'ebk-pending-cluster',plan:'free_trial',status:'ready',trial_cleanup_state:'scheduled',trial_expires_at:new Date(Date.now()-1000).toISOString(),trial_cleanup_next_at:new Date(Date.now()-1000).toISOString()});
 await f.serve();f.children[0].stdout.write('Cleanup pending: AWS environment is Launching.');f.children[0].emit('close',2);
 await vi.waitFor(()=>expect(f.rows[0].operation_running).toBe(false));
 expect(f.rows[0]).toMatchObject({status:'destroying',trial_cleanup_state:'pending',last_error:null});
 expect(f.rows[0].trial_cleanup_next_at).toBeTruthy();expect(f.rows[0].trial_cleanup_state).not.toBe('complete');
});

it('automatically deploys a changed GitHub commit to the same trial environment',async()=>{
 const f=await fixture();let sha='a'.repeat(40);
 const github=vi.fn(async url=>new Response(JSON.stringify(url.includes('/commits/')?{sha}:url.includes('/contents/')?{type:'file',size:200}:{private:false,default_branch:'main'})));
 const {url}=await f.serve({config:{githubPollIntervalMs:20},dependencies:{fetch:github}});
 const response=await f.post(url,'/api/deploy',request);expect(response.status).toBe(202);
 const project=(await response.json()).project;
 expect(f.launch.mock.calls[0][1]).toContain('a'.repeat(40));
 const ready={deployments:[{status:'Ready',health:'Green',url:'https://stable.example.com'}]};
 await writeFile(path.join(f.root,`${project.id}.json`),JSON.stringify(ready));f.children[0].emit('close',0);
 await vi.waitFor(()=>expect(f.rows[0].status).toBe('ready'));
 const deadline=f.rows[0].trial_expires_at;sha='b'.repeat(40);
 await vi.waitFor(()=>expect(f.launch).toHaveBeenCalledTimes(2));
 const args=f.launch.mock.calls[1][1];expect(args).toContain('--commit');expect(args).toContain(sha);
 expect(args).toContain(project.application_name);expect(args).toContain(project.environment_name);
 expect(f.rows[0].live_url).toBe('https://stable.example.com');
 f.children[1].emit('close',0);await vi.waitFor(()=>expect(f.rows[0].status).toBe('ready'));
 expect(f.rows[0].trial_expires_at).toBe(deadline);
 sha='c'.repeat(40);f.advance(1800001);
 await new Promise(resolve=>setTimeout(resolve,80));expect(f.launch.mock.calls.filter(call=>call[1][2]==='deploy')).toHaveLength(2);
});

it('reserves clone capacity and manages its deadline independently of the source',async()=>{
 const f=await fixture(),source={id:'11111111-1111-4111-8111-111111111111',user_id:'owner',mode:'live',name:'source',repository:request.repository,region:'us-east-2',port:3000,application_name:'app',environment_name:'source-env',plan:'free_trial',status:'ready',replicas:1,revision:1,variable_names:[],trial_cleanup_state:'scheduled',trial_expires_at:new Date(Date.now()+600000).toISOString(),trial_cleanup_next_at:new Date(Date.now()+600000).toISOString()};
 f.rows.push(source);const deadline=source.trial_expires_at;
 const {url}=await f.serve({config:{testingEmails:['owner@example.test']}});
 const response=await f.post(url,`/api/projects/${source.id}/clone`,{confirmation:'source-env',name:'clone-app',confirmCosts:true});
 expect(response.status).toBe(202);const clone=(await response.json()).project;expect(clone.id).not.toBe(source.id);expect(clone.operation_running).toBe(true);
 expect(f.admin.database.rpc.mock.calls[0][0]).toBe('ebkit_reserve_trial');expect(f.launch.mock.calls[0][1]).toContain('--max-replicas');
 await writeFile(path.join(f.root,`${clone.id}.json`),JSON.stringify({deployments:[{environment_name:clone.environment_name,status:'Ready',health:'Green',url:'https://clone.example'}]}));
 f.children[0].emit('close',0);await vi.waitFor(()=>expect(f.rows[1].status).toBe('ready'));
 expect(source.trial_expires_at).toBe(deadline);expect(f.rows[1].trial_started_at).toBeTruthy();expect(f.rows[1].trial_cleanup_state).toBe('scheduled');
});
