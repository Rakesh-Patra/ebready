import { afterEach, expect, it, vi } from 'vitest';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { createUploads } from './uploads.mjs';
const roots=[],user={id:'production-owner'};
const good='FROM node:22-trixie-slim AS build\nWORKDIR /app\nCOPY app.js /app/app.js\nFROM gcr.io/distroless/nodejs22-debian13:nonroot\nWORKDIR /app\nCOPY --from=build /app /app\nUSER 65532:65532\nEXPOSE 8080\nENTRYPOINT ["/nodejs/bin/node"]\nCMD ["app.js"]\n';
const bad='FROM node:22-trixie-slim\nCOPY app.js /app/app.js\nUSER root\nEXPOSE 8080\nCMD ["node","app.js"]\n';
afterEach(async()=>{for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});
async function fixture(fallback=good){
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-production-gate-'));roots.push(root);
  const gordonGenerate=vi.fn(async()=>({dockerfile:bad})),geminiFetch=vi.fn(async()=>new Response(JSON.stringify({candidates:[{content:{parts:[{text:JSON.stringify({dockerfile:fallback})}]}}]})));
  const manager=createUploads({jobRoot:root,python:process.env.EBKIT_TEST_PYTHON||'python',geminiKey:'test-only-key'}, {
    inspectZip:async(_archive,directory)=>{await mkdir(directory,{recursive:true});await writeFile(path.join(directory,'app.js'),'console.log("sample")');return {dockerfile:null,port:8080,files:[],scan:{language:'node'},environmentScanComplete:true};},
    reviewedRecipe:async()=>({dockerfile:null}),gordonGenerate,geminiFetch,
    sourceScout:async()=>({gate_passed:true,available:true,critical_count:0,high_count:0,details:[],summary:'mock source pass'})
  });
  const upload=await manager.upload(Readable.from([Buffer.from('mock')]),user);
  return {root,manager,upload,gordonGenerate,geminiFetch};
}
it('uses the real shared gate to reject unsafe Gordon and validate the Gemini replacement',async()=>{
  const {manager,upload,gordonGenerate,geminiFetch}=await fixture();
  const result=await manager.prepare(upload.uploadId,user,8080,true);
  expect(gordonGenerate).toHaveBeenCalledTimes(2);expect(geminiFetch).toHaveBeenCalledOnce();
  expect(result.generator).toBe('gemini');expect(result.quality).toMatchObject({passed:true,distroless:true,build:'not_verified',runtime:'not_verified',imageSecurity:'not_verified'});
  const item=await manager.load(upload.uploadId,user);expect(await readFile(path.join(item.directory,'project','Dockerfile'),'utf8')).toBe(good);
},20000);
it('does not write an invalid fallback Dockerfile or create a review hash',async()=>{
  const {manager,upload}=await fixture(bad);
  await expect(manager.prepare(upload.uploadId,user,8080,true)).rejects.toMatchObject({status:422});
  const item=await manager.load(upload.uploadId,user);expect(item.record.reviewHash).toBeUndefined();
  await expect(readFile(path.join(item.directory,'project','Dockerfile'))).rejects.toMatchObject({code:'ENOENT'});
},20000);
