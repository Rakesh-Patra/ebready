import { describe,it,expect } from 'vitest';
import { inspectEnvironment,requireEnvironment,discoverEnvironment } from './environment.mjs';
describe('repository environment discovery',()=>{
 it('detects required database names without leaking example values',()=>{
  const variables=inspectEnvironment([{path:'.env.example',text:'POSTGRES_URL=postgres://example-secret@host/db\nPORT=8080\n# optional\nANALYTICS_KEY=\nLOG_LEVEL=info'}]);
  expect(variables.find(x=>x.name==='POSTGRES_URL').required).toBe(true);
  expect(variables.find(x=>x.name==='ANALYTICS_KEY').required).toBe(false);
  expect(variables.find(x=>x.name==='LOG_LEVEL').required).toBe(false);
  expect(variables.some(x=>x.name==='PORT')).toBe(false);
  expect(JSON.stringify(variables)).not.toContain('example-secret');
  expect(requireEnvironment(variables,{})).toEqual(['POSTGRES_URL']);
  expect(requireEnvironment(variables,{POSTGRES_URL:'postgres://real-value'})).toEqual([]);
 });
 it.each([['main.go','os.Getenv("POSTGRES_URL")'],['app.py','os.environ.get("DATABASE_URL")'],['server.ts','process.env.DATABASE_URL'],['App.java','System.getenv("DATABASE_URL")']])('finds connection variables in %s', (path,text)=>{
   expect(inspectEnvironment([{path,text}])[0].required).toBe(true);
 });
 it('rejects blank and placeholder values',()=>{
   const vars=[{name:'DATABASE_URL',required:true}];
   for(const value of ['', '   ', 'YOUR_DATABASE_URL', '<connection>']) expect(requireEnvironment(vars,{DATABASE_URL:value})).toEqual(['DATABASE_URL']);
 });
});

it('omits unused Compose settings when runtime references are available',()=>{
 const variables=inspectEnvironment([{path:'.env.example',text:'POSTGRES_PASSWORD=local\nBACKEND_PORT=8080'}, {path:'backend/main.go',text:'os.Getenv("POSTGRES_URL"); os.Getenv("BACKEND_PORT")'}]);
 expect(variables.map(x=>x.name)).toEqual(['BACKEND_PORT','POSTGRES_URL']);
});

it('caches names for an unchanged tree while still rechecking the tree',async()=>{
 let fileReads=0;
 const get=async url=>url.includes('/git/trees/')?{sha:'test-cache-tree',tree:[{path:'.env.example',type:'blob',size:13}]}:(fileReads++,{encoding:'base64',content:Buffer.from('DATABASE_URL=').toString('base64'),size:13});
 const first=await discoverEnvironment('https://api.github.com/repos/cache/test','main',get);
 const second=await discoverEnvironment('https://api.github.com/repos/cache/test','main',get);
 expect(second).toEqual(first);expect(fileReads).toBe(1);
});
