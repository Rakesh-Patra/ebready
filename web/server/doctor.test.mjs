import { afterEach, expect, it } from 'vitest';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { createUploads } from './uploads.mjs';

const roots=[],user={id:'doctor-owner'};
afterEach(async()=>{for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});

it('runs the shared Python doctor, checks ownership and invalidates previous approval after repairs',async()=>{
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-doctor-'));roots.push(root);
  const dockerfile='FROM python:3.11-slim\nCOPY . .\nEXPOSE 8000\nCMD ["uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8080"]\n';
  const manager=createUploads({jobRoot:root,python:process.env.EBKIT_TEST_PYTHON||'python'},{
    inspectZip:async(_archive,directory)=>{
      await mkdir(directory,{recursive:true});await writeFile(path.join(directory,'Dockerfile'),dockerfile);
      await writeFile(path.join(directory,'app.py'),'import uvicorn\nuvicorn.run("app:app", host="localhost")\n');
      return {dockerfile,port:8080,files:[],scan:{},environmentScanComplete:true};
    },sourceScout:async()=>({gate_passed:true,available:true,critical_count:0,high_count:0,details:[],summary:'passed'})
  });
  const upload=await manager.upload(Readable.from([Buffer.from('mock')]),user);
  const prepared=await manager.prepare(upload.uploadId,user,8080);
  await expect(manager.doctor(upload.uploadId,{id:'another-user'},{port:8080})).rejects.toMatchObject({status:404});
  const report=await manager.doctor(upload.uploadId,user,{port:8080});
  expect(report.patches.map(p=>p.path)).toEqual(expect.arrayContaining(['Dockerfile','app.py']));
  const file=path.join(root,'uploads',upload.uploadId,'project','Dockerfile');
  expect(await readFile(file,'utf8')).toContain('127.0.0.1');
  await expect(manager.doctor(upload.uploadId,user,{port:8080,apply:true,planHash:report.planHash})).rejects.toMatchObject({status:400});
  await expect(manager.doctor(upload.uploadId,user,{port:8080,apply:true,reviewed:true,planHash:'b'.repeat(64)})).rejects.toMatchObject({status:409});
  const applied=await manager.doctor(upload.uploadId,user,{port:8080,apply:true,reviewed:true,planHash:report.planHash});
  expect(applied.applied).toEqual(expect.arrayContaining(['Dockerfile','app.py']));
  expect(applied.findings).toEqual([]);expect(applied.verification.runtime).toBe('not_verified');
  expect(applied.inspection).toBeUndefined();
  await expect(manager.reviewed(upload.uploadId,user,{reviewed:true,reviewHash:prepared.reviewHash})).rejects.toMatchObject({status:400});
  const refreshed=await manager.prepare(upload.uploadId,user,8080);
  expect(refreshed.dockerfile).toContain('0.0.0.0');expect(refreshed.dockerfile).toContain('EXPOSE 8080');
  await expect(manager.reviewed(upload.uploadId,user,{reviewed:true,reviewHash:refreshed.reviewHash})).resolves.toBeTruthy();
},30000);
