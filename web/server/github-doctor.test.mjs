import { afterEach, expect, it, vi } from 'vitest';
import { mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { createUploads } from './uploads.mjs';
import { createWorker } from './worker.mjs';
const roots=[],servers=[],user={id:'owner',email:'owner@example.test'};
const commit='a'.repeat(40);
async function fixture(){
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-github-doctor-'));roots.push(root);
  const githubSnapshot=vi.fn(async(repository,directory)=>{await mkdir(directory,{recursive:true});await writeFile(path.join(directory,'app.py'),'app.run(host="localhost")\n');return {repository,commit,port:8080,dockerfile:null,files:[],scan:{},environmentScanComplete:true};});
  return {root,githubSnapshot,manager:createUploads({jobRoot:root},{githubSnapshot})};
}
afterEach(async()=>{for(const server of servers.splice(0)){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});
it('imports a missing-Dockerfile GitHub project into the same owned doctor workflow',async()=>{
  const {manager,githubSnapshot}=await fixture();
  const result=await manager.importGithub(user,'https://github.com/owner/repo.git');
  expect(result).toMatchObject({repository:'https://github.com/owner/repo',commit,hasDockerfile:false,port:8080});
  expect(githubSnapshot.mock.calls[0][0]).toBe('https://github.com/owner/repo');
  expect((await manager.load(result.uploadId,user)).record).toMatchObject({userId:user.id,repository:result.repository,commit});
  await expect(manager.load(result.uploadId,{id:'other'})).rejects.toMatchObject({status:404});
  await expect(manager.importGithub(user,'https://attacker.test/owner/repo')).rejects.toMatchObject({status:400});
  expect(githubSnapshot).toHaveBeenCalledOnce();
});
it('requires authentication and approval before GitHub import and never launches cloud commands',async()=>{
  const {root,githubSnapshot}=await fixture(),spawn=vi.fn();
  const server=await createWorker({trialsEnabled:false,liveEnabled:true,jobRoot:root,allowedEmails:[user.email],origins:[]},{admin:{},recover:false,spawn,githubSnapshot,authenticate:async token=>token==='approved'?user:{id:'other',email:'other@example.test'}});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));servers.push(server);
  const url=`http://127.0.0.1:${server.address().port}/api/repository/import`;
  const post=token=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},body:JSON.stringify({repository:'https://github.com/owner/repo'})});
  expect((await post()).status).toBe(401);expect((await post('other')).status).toBe(403);
  expect(githubSnapshot).not.toHaveBeenCalled();
  const response=await post('approved');expect(response.status).toBe(200);expect(await response.json()).toMatchObject({commit,hasDockerfile:false});
  expect(spawn).not.toHaveBeenCalled();
});
