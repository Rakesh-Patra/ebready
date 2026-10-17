import {afterEach, expect, it} from 'vitest';
import {createWorker} from './worker.mjs';
let server;
afterEach(async()=>{if(server)await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});});
it('blocks authentication on a public demo while keeping health checks available',async()=>{
 server=await createWorker({publicDemo:true,liveEnabled:false,origins:[],allowedEmails:[]},{recover:false});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const url=`http://127.0.0.1:${server.address().port}`;
 expect((await fetch(url+'/api/health')).status).toBe(200);
 for(const method of ['GET','POST'])expect((await fetch(url+'/insforge/api/auth/sessions',{method})).status).toBe(403);
 for(const route of ['/api/deploy','/api/repository','/api/uploads'])expect((await fetch(url+route,{method:'POST'})).status).toBe(403);
});
