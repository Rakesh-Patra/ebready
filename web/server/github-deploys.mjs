import path from 'node:path';
import {mkdir} from 'node:fs/promises';
import {atomicJSON,readJSON} from './jobs.mjs';

const shaPattern=/^[0-9a-f]{40}$/i;
export async function githubHead(repository,request=fetch,token){
  const match=repository?.match(/^https:\/\/github\.com\/([A-Za-z0-9-]+)\/([A-Za-z0-9_.-]+?)(?:\.git)?\/?$/);
  if(!match||['.','..'].includes(match[2]))throw new Error('Invalid GitHub repository.');
  const api=`https://api.github.com/repos/${match[1]}/${match[2]}`;
  async function get(url){
    const response=await request(url,{headers:{Accept:'application/vnd.github+json','User-Agent':'EBKit',...(token?{Authorization:`Bearer ${token}`}:{})},redirect:'error',signal:AbortSignal.timeout(10000)});
    if(!response.ok)throw new Error(`GitHub watch request failed (${response.status}).`);
    return response.json();
  }
  const metadata=await get(api);
  if(metadata.private||metadata.archived||!metadata.default_branch)throw new Error('Repository must be active and public.');
  const commit=await get(`${api}/commits/${encodeURIComponent(metadata.default_branch)}`);
  if(!shaPattern.test(commit.sha??''))throw new Error('GitHub returned an invalid commit.');
  return {branch:metadata.default_branch,sha:commit.sha.toLowerCase()};
}

export function createGithubDeploys({directory,list,run,validate,isBusy,eligible=()=>true,request=fetch,token,onError=()=>{}}){
  let ticking=false;
  const file=path.join(directory,'github-watch.json');
  async function load(){
    const state=await readJSON(file).catch(error=>{if(error.code==='ENOENT')return {};throw error;});
    if(!state||Array.isArray(state)||typeof state!=='object')throw new Error('GitHub watch state is invalid.');
    return state;
  }
  async function record(project,head){
    await mkdir(directory,{recursive:true,mode:0o700});
    const state=await load();
    state[project.id]={repository:project.repository,...head};
    await atomicJSON(file,state);
  }
  async function tick(){
    if(ticking||isBusy())return;
    ticking=true;
    try{
      const projects=await list();
      for(const project of projects){
        if(isBusy())break;
        if(project.mode!=='live'||!['ready','failed'].includes(project.status)||project.operation_running||!project.repository?.startsWith('https://github.com/')||!eligible(project))continue;
        try{
          const head=await githubHead(project.repository,request,token);
          const previous=(await load())[project.id];
          // First observation of an older deployment establishes a baseline.
          if(!previous||previous.repository!==project.repository){await record(project,head);continue;}
          if(previous.sha===head.sha)continue;
          await validate(project);
          if(isBusy()||!eligible(project))break;
          await run(project,head);
          // Persist attempted commits: failures do not create an endless build loop.
          await record(project,head);
          break;
        }catch(error){await onError(project,error);}
      }
    }finally{ticking=false;}
  }
  return {tick,record,head:repository=>githubHead(repository,request,token)};
}
