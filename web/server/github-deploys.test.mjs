import {afterEach,expect,it,vi} from 'vitest';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createGithubDeploys,githubHead} from './github-deploys.mjs';
const roots=[];
afterEach(async()=>{for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});
const A='a'.repeat(40),B='b'.repeat(40),C='c'.repeat(40);
async function fixture(){
 const directory=await mkdtemp(path.join(tmpdir(),'ebkit-github-watch-'));roots.push(directory);
 const project={id:'owned',mode:'live',status:'ready',repository:'https://github.com/owner/repo',application_name:'app',environment_name:'env',port:3000};
 let sha=A,busy=false,eligible=true;
 const request=vi.fn(async url=>new Response(JSON.stringify(url.includes('/commits/')?{sha}:{private:false,default_branch:'main'})));
 const run=vi.fn(async()=>{}),validate=vi.fn(async()=>{}),list=vi.fn(async()=>[project]),onError=vi.fn();
 const options={directory,list,run,validate,request,isBusy:()=>busy,eligible:()=>eligible,onError};
 const watcher=createGithubDeploys(options);
 return {watcher,options,project,run,request,validate,onError,head:value=>sha=value,busy:value=>busy=value,eligible:value=>eligible=value};
}
it('deploys each changed commit once and persists it across worker restarts',async()=>{
 const f=await fixture();await f.watcher.record(f.project,{sha:A,branch:'main'});
 await f.watcher.tick();expect(f.run).not.toHaveBeenCalled();f.head(B);await f.watcher.tick();
 expect(f.run).toHaveBeenCalledExactlyOnceWith(f.project,{sha:B,branch:'main'});
 await f.watcher.tick();await createGithubDeploys(f.options).tick();expect(f.run).toHaveBeenCalledOnce();
 f.head(C);await f.watcher.tick();expect(f.run).toHaveBeenCalledTimes(2);
});
it('baselines existing deployments and skips ZIP, destroyed, busy and expired projects',async()=>{
 const f=await fixture();await f.watcher.tick();expect(f.run).not.toHaveBeenCalled();
 f.head(B);f.busy(true);await f.watcher.tick();f.busy(false);f.eligible(false);await f.watcher.tick();
 f.eligible(true);f.project.repository='zip:project.zip';await f.watcher.tick();
 f.project.repository='https://github.com/owner/repo';f.project.status='destroyed';await f.watcher.tick();
 expect(f.run).not.toHaveBeenCalled();f.project.status='ready';await f.watcher.tick();expect(f.run).toHaveBeenCalledOnce();
});
it('defers changes while a build runs and picks the latest commit afterward',async()=>{
 const f=await fixture();await f.watcher.record(f.project,{sha:A});f.project.operation_running=true;
 f.head(B);await f.watcher.tick();f.head(C);f.project.operation_running=false;await f.watcher.tick();
 expect(f.run.mock.calls[0][1].sha).toBe(C);
});
it('does not retry a failed build commit automatically, but accepts a new push',async()=>{
 const f=await fixture();await f.watcher.record(f.project,{sha:A});f.head(B);await f.watcher.tick();
 f.project.status='failed';await f.watcher.tick();expect(f.run).toHaveBeenCalledOnce();
 f.head(C);await f.watcher.tick();expect(f.run).toHaveBeenCalledTimes(2);
});
it('does not launch when GitHub or deployment validation fails',async()=>{
 const f=await fixture();await f.watcher.record(f.project,{sha:A});f.head(B);
 f.request.mockResolvedValueOnce(new Response('{}',{status:429}));await f.watcher.tick();
 f.validate.mockRejectedValueOnce(new Error('New environment setting required'));await f.watcher.tick();
 expect(f.run).not.toHaveBeenCalled();await f.watcher.tick();expect(f.run).toHaveBeenCalledOnce();
});
it('serializes overlapping checks and retries a launch rejected as busy',async()=>{
 const f=await fixture();await f.watcher.record(f.project,{sha:A});f.head(B);
 f.run.mockRejectedValueOnce(new Error('Busy'));await Promise.all([f.watcher.tick(),f.watcher.tick()]);
 expect(f.run).toHaveBeenCalledOnce();await f.watcher.tick();expect(f.run).toHaveBeenCalledTimes(2);
});
it('rejects private repositories, malformed commits and non-GitHub sources',async()=>{
 const request=vi.fn(async()=>new Response(JSON.stringify({private:true,default_branch:'main'})));
 await expect(githubHead('zip:foo',request)).rejects.toThrow();expect(request).not.toHaveBeenCalled();
 await expect(githubHead('https://github.com/a/b',request)).rejects.toThrow('active and public');
 request.mockResolvedValueOnce(new Response(JSON.stringify({default_branch:'main'}))).mockResolvedValueOnce(new Response(JSON.stringify({sha:'bad'})));
 await expect(githubHead('https://github.com/a/b',request)).rejects.toThrow('invalid commit');
});
