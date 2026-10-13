import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { PassThrough } from 'node:stream';
import { EventEmitter } from 'node:events';
import { fileURLToPath } from 'node:url';
import { createUploads, validateDockerfile } from './uploads.mjs';
import { createWorker } from './worker.mjs';
import { readJSON, atomicJSON } from './jobs.mjs';
const user={id:'owner',email:'owner@example.test'},dockerfile='FROM node:22-alpine\nWORKDIR /app\nCOPY . .\nUSER node\nEXPOSE 3000\nCMD ["node","app.js"]\n';
const directories=[],servers=[];
async function setup(existing=true,extra={},config={}) {
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-upload-'));directories.push(root);
  const geminiFetch=vi.fn(async()=>new Response(JSON.stringify({candidates:[{content:{parts:[{text:JSON.stringify({dockerfile,notes:'Review this draft'})}]}}]})));
  const sourceScout=vi.fn(async()=>({gate_passed:true,available:true,critical_count:0,high_count:0,summary:'Source dependency security gate passed',details:[]}));
  const inspectZip=async(_archive,destination)=>{await mkdir(destination,{recursive:true});await writeFile(path.join(destination,'app.js'),'console.log("hello")');if(existing)await writeFile(path.join(destination,'Dockerfile'),dockerfile);return {scan:{language:'node',detected_start_command:'node app.js'},files:[{path:'.env.example',text:'DATABASE_URL='},{path:'app.js',text:'process.env.DATABASE_URL'}],dockerfile:existing?dockerfile:null,port:3000,environmentScanComplete:true};};
  const productionQuality=vi.fn(async()=>({passed:true,runtimeImage:'mock-runtime',distroless:false,checks:[],warnings:[],build:'not_verified',runtime:'not_verified',imageSecurity:'not_verified'})),reviewedRecipe=vi.fn(async()=>({dockerfile:null}));
  const manager=createUploads({jobRoot:root,python:process.execPath,geminiKey:'operator-test-key',...config}, {inspectZip,geminiFetch,sourceScout,productionQuality,reviewedRecipe,gordonGenerate:async()=>({available:false,dockerfile:null}),...extra});
  const uploaded=await manager.upload(Readable.from([Buffer.from('mock-zip')]),user,'demo.zip');
  return {root,manager,uploaded,geminiFetch,inspectZip,sourceScout,productionQuality,reviewedRecipe};
}
afterEach(async()=>{for(const server of servers.splice(0)){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}for(const root of directories.splice(0))await rm(root,{recursive:true,force:true});});
it('does not apply daily preparation quotas to the designated evaluator',async()=>{
  const f=await setup(false,{}, {evaluatorEmails:[user.email]});
  await atomicJSON(path.join(f.root,'ai-quota.json'),{[`${new Date().toISOString().slice(0,10)}:${user.id}`]:100});
  expect((await f.manager.prepare(f.uploaded.uploadId,user,3000,true)).generated).toBe(true);
});
it('rejects a Gordon draft with missing inputs and checks the Gemini fallback',async()=>{
  const gordonGenerate=vi.fn(async()=>({dockerfile:'FROM node:22-alpine\nCOPY frontend/dist /app\nCMD ["node","app.js"]'}));
  const f=await setup(false,{gordonGenerate});
  const draft=await f.manager.prepare(f.uploaded.uploadId,user,3000,true);
  expect(draft.generator).toBe('gemini');
  expect(f.geminiFetch).toHaveBeenCalledOnce();
});
it('retries a rejected Gordon Dockerfile with concrete validation feedback',async()=>{
  const gordonGenerate=vi.fn()
    .mockResolvedValueOnce({available:true,dockerfile:'FROM node:22-alpine\nCOPY frontend/dist /app\nCMD ["node","app.js"]'})
    .mockResolvedValueOnce({available:true,dockerfile});
  const f=await setup(false,{gordonGenerate});
  const draft=await f.manager.prepare(f.uploaded.uploadId,user,3000,true);
  expect(draft.generator).toBe('gordon');expect(draft.dockerfile).toBe(dockerfile);
  expect(gordonGenerate).toHaveBeenCalledTimes(2);
  expect(gordonGenerate.mock.calls[0][0]).toContain('one production Dockerfile');
  expect(gordonGenerate.mock.calls[1][0]).toContain('failed EBKit validation');
  expect(gordonGenerate.mock.calls[1][0]).toContain('frontend/dist');
  expect(f.geminiFetch).not.toHaveBeenCalled();
});
it('includes root-relative multi-tier and supplied lockfile details in the hosted Gordon prompt',async()=>{
  const gordonGenerate=vi.fn(async()=>({available:true,dockerfile}));
  const inspectZip=async(_archive,destination)=>{
    await mkdir(destination,{recursive:true});await writeFile(path.join(destination,'app.js'),'console.log("hello")');
    return {scan:{language:'node',architecture:'MULTI_TIER',services:['frontend','backend'],stateful_services:['postgres'],detected_start_command:'node app.js'},files:[
      {path:'backend/package.json',text:'{"scripts":{"start":"node app.js"}}'},
      {path:'backend/package-lock.json',text:'{"lockfileVersion":3}'},
      {path:'frontend/package.json',text:'{"scripts":{"build":"vite build"}}'},
    ],dockerfile:null,port:3000,environmentScanComplete:true};
  };
  const f=await setup(false,{gordonGenerate,inspectZip});
  const draft=await f.manager.prepare(f.uploaded.uploadId,user,3000,true);
  expect(draft.generator).toBe('gordon');
  const prompt=gordonGenerate.mock.calls[0][0];
  expect(prompt).toContain('one root Dockerfile');
  expect(prompt).toContain('repository-root build context');
  expect(prompt).toContain('backend/package-lock.json');
  expect(prompt).toContain('"frontend"');
  expect(prompt).toContain('"postgres"');
});
it.each([[false,5,true],[true,5,false],[true,20,true]])('enforces separate normal and testing AI limits (%s, %s)',async(testing,count,blocked)=>{
  const f=await setup(false,{}, {testingEmails:testing?[user.email]:[]});
  await atomicJSON(path.join(f.root,'ai-quota.json'),{[`${new Date().toISOString().slice(0,10)}:${user.id}`]:count});
  const result=f.manager.prepare(f.uploaded.uploadId,user,3000,true);
  if(blocked){await expect(result).rejects.toMatchObject({status:429});expect(f.geminiFetch).not.toHaveBeenCalled();}
  else{expect((await result).generated).toBe(true);expect(f.geminiFetch).toHaveBeenCalledOnce();}
});
it('returns an existing Dockerfile and required names without calling Gemini or GitHub',async()=> {
  const {manager,uploaded,geminiFetch}=await setup();const draft=await manager.prepare(uploaded.uploadId,user);
  expect(draft.dockerfile).toBe(dockerfile);expect(draft.environment).toEqual([{name:'DATABASE_URL',required:true,source:'.env.example'}]);
  expect(geminiFetch).not.toHaveBeenCalled();await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:false,reviewHash:draft.reviewHash})).rejects.toMatchObject({status:400});
  await expect(manager.reviewed(uploaded.uploadId,{id:'other'},{reviewed:true,reviewHash:draft.reviewHash})).rejects.toMatchObject({status:404});
});
it('uses the operator key in a Gemini header and requires consent/review',async()=> {
  const {manager,uploaded,geminiFetch,root}=await setup(false);
  await expect(manager.prepare(uploaded.uploadId,user,3000)).rejects.toMatchObject({status:400});
  expect(geminiFetch).not.toHaveBeenCalled();const draft=await manager.prepare(uploaded.uploadId,user,3000,true);
  expect(draft.generated).toBe(true);const [url,request]=geminiFetch.mock.calls[0];expect(url).not.toContain('operator-test-key');expect(request.headers['x-goog-api-key']).toBe('operator-test-key');
  expect(draft.generator).toBe('gemini');
  expect(JSON.stringify(draft)).not.toContain('operator-test-key');const item=await manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash});
  await writeFile(path.join(item.directory,'project','Dockerfile'),dockerfile+'# changed');
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash})).rejects.toMatchObject({status:400});
  expect(JSON.stringify(await readJSON(path.join(root,'ai-quota.json')))).not.toContain('operator-test-key');
});
it('uses Gordon first without calling Gemini when Gordon returns a valid draft',async()=> {
  const gordonGenerate=vi.fn(async()=>({available:true,dockerfile}));
  const {manager,uploaded,geminiFetch}=await setup(false,{gordonGenerate});
  const draft=await manager.prepare(uploaded.uploadId,user,3000,true);
  expect(draft.generator).toBe('gordon');expect(draft.dockerfile).toBe(dockerfile);
  expect(gordonGenerate).toHaveBeenCalledOnce();expect(geminiFetch).not.toHaveBeenCalled();
});
it('runs source Scout after writing the Gordon draft and blocks a failed gate',async()=>{
  const events=[],gordonGenerate=async()=>{events.push('gordon');return {available:true,dockerfile};};
  const sourceScout=vi.fn(async directory=>{events.push('scout');expect(await readFile(path.join(directory,'Dockerfile'),'utf8')).toBe(dockerfile);return {gate_passed:false,available:true,critical_count:1,high_count:7,summary:'1 Critical, 7 High',gate_reason:'Critical CVEs exceed threshold',details:[{id:'CVE-test',severity:'critical',packages:['sample'],paths:['package.json'],fixed_version:'2.0'}]};});
  const {manager,uploaded}=await setup(false,{gordonGenerate,sourceScout});
  const draft=await manager.prepare(uploaded.uploadId,user,3000,true);
  expect(events).toEqual(['gordon','scout']);expect(draft.scout.gate_passed).toBe(false);
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash})).rejects.toMatchObject({status:422});
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash},false)).resolves.toBeTruthy();
});
it('requires an explicit acknowledged Scout bypass and records security as NOT VERIFIED',async()=>{
  const {manager,uploaded,sourceScout}=await setup();
  await expect(manager.prepare(uploaded.uploadId,user,3000,false,true,false)).rejects.toMatchObject({status:400});
  const draft=await manager.prepare(uploaded.uploadId,user,3000,false,true,true);
  expect(draft.scout.bypassed).toBe(true);expect(draft.scout.summary).toContain('NOT VERIFIED');expect(sourceScout).not.toHaveBeenCalled();
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash})).resolves.toBeTruthy();
});
it('does not accept a claimed Scout pass that exceeds the security thresholds',async()=>{
  const {manager,uploaded}=await setup(true,{sourceScout:async()=>({gate_passed:true,available:true,critical_count:2,high_count:0,summary:'claimed pass',details:[]})});
  const draft=await manager.prepare(uploaded.uploadId,user);
  expect(draft.scout.gate_passed).toBe(false);
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true,reviewHash:draft.reviewHash,scout:{gate_passed:true}})).rejects.toMatchObject({status:422});
});
it('fails closed when Scout is unavailable or crashes',async()=>{
  const {manager,uploaded}=await setup(true,{sourceScout:async()=>{throw new Error('Scout unavailable');}});
  const draft=await manager.prepare(uploaded.uploadId,user);
  expect(draft.scout.gate_passed).toBe(false);expect(draft.scout.summary).toContain('NOT VERIFIED');
});
it.each(['failed','invalid'])('falls back to Gemini after a %s Gordon attempt',async kind=>{
  const gordonGenerate=vi.fn(async()=>{if(kind==='failed')throw new Error('Gordon unavailable');return {available:true,dockerfile:'invalid'};});
  const {manager,uploaded,geminiFetch}=await setup(false,{gordonGenerate});
  const draft=await manager.prepare(uploaded.uploadId,user,3000,true);
  expect(draft.generator).toBe('gemini');expect(gordonGenerate).toHaveBeenCalledTimes(kind==='failed'?1:2);expect(geminiFetch).toHaveBeenCalledOnce();
  expect(gordonGenerate.mock.invocationCallOrder[0]).toBeLessThan(geminiFetch.mock.invocationCallOrder[0]);
});
it('handles Gemini 429 without claiming preparation succeeded or exposing the key',async()=> {
  const {manager,uploaded}=await setup(false,{geminiFetch:async()=>new Response('{}',{status:429})});
  await expect(manager.prepare(uploaded.uploadId,user,3000,true)).rejects.toMatchObject({status:429});
  await expect(manager.reviewed(uploaded.uploadId,user,{reviewed:true})).rejects.toMatchObject({status:400});
});
it('rejects expired uploads and oversize requests before extraction',async()=> {
  const {manager,uploaded,root}=await setup();const file=path.join(root,'uploads',uploaded.uploadId,'record.json'),record=await readJSON(file);
  await atomicJSON(file,{...record,expires:Date.now()-1});await expect(manager.load(uploaded.uploadId,user)).rejects.toMatchObject({status:410});
  await expect(manager.upload(Readable.from([Buffer.alloc(25*1024*1024+1)]),user)).rejects.toMatchObject({status:413});
});
it('rejects dangerous Dockerfile secret copies and invalid AI output',()=> {
  expect(()=>validateDockerfile('not a dockerfile')).toThrow();
  expect(()=>validateDockerfile('FROM alpine:3.22\nCOPY .env /app/.env\nCMD ["sh"]')).toThrow('secret');
});
it('authenticates ZIP upload before inspecting it and refuses client-supplied local paths',async()=> {
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-upload-api-'));directories.push(root);const inspectZip=vi.fn(),spawn=vi.fn();
  const server=await createWorker({trialsEnabled:false,liveEnabled:true,jobRoot:root,allowedEmails:['owner@example.test'],origins:[]},{admin:{},recover:false,inspectZip,spawn,authenticate:async()=>({id:'other',email:'other@example.test'})});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));servers.push(server);const url=`http://127.0.0.1:${server.address().port}`;
  expect((await fetch(url+'/api/uploads',{method:'POST',headers:{'Content-Type':'application/zip'},body:'zip'})).status).toBe(401);
  expect((await fetch(url+'/api/uploads',{method:'POST',headers:{'Content-Type':'application/zip',Authorization:'Bearer test'},body:'zip'})).status).toBe(403);
  expect(inspectZip).not.toHaveBeenCalled();expect(spawn).not.toHaveBeenCalled();
});
it('hands a reviewed ZIP to the CLI without GitHub calls and reuses its owned snapshot on redeploy',async()=>{
  const fixture=await setup(),rows=[],children=[],github=vi.fn();
  const admin={database:{from(){let payload,operation='select',filters=[];
    const execute=()=>{const matches=rows.filter(row=>filters.every(([key,value,negative])=>negative?row[key]!==value:row[key]===value));if(operation==='insert'){rows.push(...payload);return {data:payload,error:null};}if(operation==='update')matches.forEach(row=>Object.assign(row,payload));return {data:matches,error:null};};
    const builder={select:()=>builder,eq:(key,value)=>{filters.push([key,value,false]);return builder;},neq:(key,value)=>{filters.push([key,value,true]);return builder;},limit:async()=>execute(),insert:value=>{operation='insert';payload=value;return builder;},update:value=>{operation='update';payload=value;return builder;},then:(resolve,reject)=>Promise.resolve(execute()).then(resolve,reject)};return builder;
  }}};
  const launch=vi.fn(()=>{const child=new EventEmitter();child.stdin=new PassThrough();child.stdout=new PassThrough();child.stderr=new PassThrough();child.kill=vi.fn();children.push(child);return child;});
  const python=process.env.EBKIT_TEST_PYTHON||(process.platform==='win32'?fileURLToPath(new URL('../../.venv/Scripts/python.exe',import.meta.url)):'python3');
  const server=await createWorker({trialsEnabled:false,liveEnabled:true,jobRoot:fixture.root,python,allowedEmails:[user.email],origins:[]},{admin,recover:false,spawn:launch,authenticate:async()=>user,inspectZip:fixture.inspectZip,fetch:github,sourceScout:fixture.sourceScout,productionQuality:fixture.productionQuality,reviewedRecipe:fixture.reviewedRecipe});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));servers.push(server);const url=`http://127.0.0.1:${server.address().port}`;
  const post=(route,body)=>fetch(url+route,{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/json'},body:JSON.stringify(body)});
  const uploaded=await (await fetch(url+'/api/uploads',{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/zip','x-ebkit-filename':'project.zip'},body:'mock zip'})).json();
  const draft=await(await post(`/api/uploads/${uploaded.uploadId}/prepare`,{})).json();
  const body={uploadId:uploaded.uploadId,reviewHash:draft.reviewHash,name:'zip-app',region:'us-east-2',port:3000,confirmCosts:true};
  expect((await post('/api/deploy',body)).status).toBe(400);expect(launch).not.toHaveBeenCalled();
  expect((await post('/api/deploy',{...body,reviewed:true})).status).toBe(400);expect(launch).not.toHaveBeenCalled();
  const download=await post(`/api/uploads/${uploaded.uploadId}/download`,{reviewed:true,reviewHash:draft.reviewHash});expect(download.status).toBe(200);expect(Buffer.from(await download.arrayBuffer()).subarray(0,2).toString()).toBe('PK');
  expect((await post('/api/deploy',{...body,reviewed:true,environment:{DATABASE_URL:'test-database-secret'}})).status).toBe(202);
  expect(launch.mock.calls[0][1]).toContain('--reviewed');expect(launch.mock.calls[0][1][3]).toMatch(/prepared\.zip$/);
  expect(launch.mock.calls[0][2].shell).toBe(false);expect(rows[0].repository).toBe('zip:project.zip');
  children[0].emit('close',1);await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));
  expect(JSON.stringify(rows)).not.toContain('test-database-secret');
  expect((await post(`/api/projects/${rows[0].id}/deploy`,{confirmCosts:true})).status).toBe(202);
  expect(launch.mock.calls[1][1]).toContain(rows[0].environment_name);children[1].emit('close',1);await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));
  const replacement=await(await fetch(url+'/api/uploads',{method:'POST',headers:{Authorization:'Bearer test','Content-Type':'application/zip','x-ebkit-filename':'updated.zip'},body:'new mock zip'})).json();
  const replacementDraft=await(await post(`/api/uploads/${replacement.uploadId}/prepare`,{port:3000})).json();
  const updateBody={uploadId:replacement.uploadId,reviewHash:replacementDraft.reviewHash,reviewed:true,port:3000,confirmCosts:true};
  const target=rows[0].environment_name,application=rows[0].application_name;
  expect((await post(`/api/projects/${rows[0].id}/deploy`,{...updateBody,reviewed:false})).status).toBe(400);
  expect((await post(`/api/projects/${rows[0].id}/deploy`,{...updateBody,port:8080,environment:{DATABASE_URL:'replacement-secret'}})).status).toBe(400);
  expect(launch).toHaveBeenCalledTimes(2);
  rows[0].status='ready';rows[0].live_url='https://existing.example.com';
  expect((await post(`/api/projects/${rows[0].id}/deploy`,updateBody)).status).toBe(202);
  expect(rows).toHaveLength(1);expect(rows[0].repository).toBe('zip:updated.zip');
  expect(rows[0].live_url).toBe('https://existing.example.com');expect(rows[0].variable_names).toContain('DATABASE_URL');
  expect(launch.mock.calls[2][1]).toContain(target);expect(launch.mock.calls[2][1]).toContain(application);
  expect(launch.mock.calls[2][1][3]).not.toBe(launch.mock.calls[0][1][3]);
  expect(await readJSON(path.join(fixture.root,`${rows[0].id}.source.json`))).toMatchObject({uploadId:replacement.uploadId});
  children[2].emit('close',1);await vi.waitFor(()=>expect(rows[0].operation_running).toBe(false));
  expect(JSON.stringify(rows)).not.toContain('replacement-secret');
  expect(github).not.toHaveBeenCalled();
  expect((await post(`/api/projects/${rows[0].id}/status`,{sourceArchive:'C:/private.zip'})).status).toBe(400);
  await fixture.manager.removeProject(rows[0].id);
  await expect(readJSON(path.join(fixture.root,'uploads',uploaded.uploadId,'record.json'))).rejects.toMatchObject({code:'ENOENT'});
  await expect(readJSON(path.join(fixture.root,'uploads',replacement.uploadId,'record.json'))).rejects.toMatchObject({code:'ENOENT'});
  await expect(fixture.manager.load(fixture.uploaded.uploadId,user)).resolves.toBeTruthy();
},20000);
