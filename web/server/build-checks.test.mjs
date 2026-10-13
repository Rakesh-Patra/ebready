import { it, expect, afterEach } from 'vitest';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { checkBuildInputs } from './build-checks.mjs';
const roots=[];
async function project(deps={}) {
  const root=await mkdtemp(path.join(tmpdir(),'ebkit-build-check-')); roots.push(root);
  await mkdir(path.join(root,'frontend'));
  await writeFile(path.join(root,'frontend/package.json'),JSON.stringify({devDependencies:deps}));
  return root;
}
afterEach(async()=>{for(const root of roots.splice(0))await rm(root,{recursive:true,force:true});});
it('blocks the observed dependency conflict before calling AI or AWS',async()=>{
  await expect(checkBuildInputs(await project({vite:'^8.1.0','@vitejs/plugin-react':'^4.3.1'}))).rejects.toThrow('conflicts');
});
it('blocks Node 18 with modern Vite',async()=>{
  await expect(checkBuildInputs(await project({vite:'^8.1.0'}),'FROM node:18-alpine\nCOPY . .\nCMD ["node","app"]')).rejects.toThrow('Node');
});
it('blocks missing generated assets and accepts a build-stage copy',async()=>{
  const root=await project();
  await expect(checkBuildInputs(root,'FROM node:22-alpine\nCOPY frontend/dist /app\nCMD ["node","app"]')).rejects.toThrow('missing source');
  await expect(checkBuildInputs(root,'FROM node:22-alpine AS frontend\nCOPY frontend/ /app\nCOPY --from=frontend /app/dist /site\nCMD ["node","app"]')).resolves.toBeUndefined();
});
it('supports JSON copy and optional wildcard lockfiles',async()=>{
  const root=await project();
  await expect(checkBuildInputs(root,'COPY frontend/package.json frontend/package-lock.json* /app/\nCOPY ["frontend/package.json", "/app/"]')).resolves.toBeUndefined();
  await expect(checkBuildInputs(root,'COPY ["missing.txt", "/app/"]')).rejects.toThrow('missing source');
});
