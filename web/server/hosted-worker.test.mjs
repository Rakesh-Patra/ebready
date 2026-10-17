import {afterEach,expect,it,vi} from 'vitest';
import {createWorker} from './worker.mjs';
import {createTrials} from './trials.mjs';
const servers=[];
afterEach(async()=>{for(const s of servers.splice(0)){s.closeAllConnections();await new Promise(r=>s.close(r));}});
it.each([false,undefined])('blocks hosted deployment for unverified email %s',async verified=>{
 const server=await createWorker({hostedRegion:'us-east-2',liveEnabled:true,trialsEnabled:false,allowedEmails:['approved@example.invalid'],origins:[]},{admin:{},recover:false,authenticate:async()=>({id:'user',email:'approved@example.invalid',emailVerified:verified})});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));servers.push(server);
 const response=await fetch(`http://127.0.0.1:${server.address().port}/api/profiles`,{method:'POST',headers:{Authorization:'Bearer synthetic'},body:'{}'});
 expect(response.status).toBe(403);expect((await response.json()).error).toContain('Verify your email');
});
it('enforces the hosted slot even for designated repeat testers',async()=>{
 const query={select:()=>query,eq:()=>query,limit:async()=>({data:[{id:'other',status:'ready',plan:'free_trial',trial_cleanup_state:'scheduled'}],error:null})};
 const admin={database:{from:()=>query,rpc:vi.fn()}};
 const trials=createTrials({admin,save:vi.fn(),run:vi.fn(),isBusy:()=>false,strictCapacity:true,capacity:1,testingEmails:['approved@example.invalid']});
 await expect(trials.reserve({user_id:'user'},{id:'user',email:'approved@example.invalid'})).rejects.toMatchObject({status:429});
 expect(admin.database.rpc).not.toHaveBeenCalled();
});
