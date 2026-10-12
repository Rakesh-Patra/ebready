import {expect,it,vi} from 'vitest';
import {createTrials} from './trials.mjs';
const time=Date.parse('2026-10-12T12:00:00Z');
function fixture(overrides={}){
  let clock=time,busy=false;
  const rows=[{id:'one',plan:'free_trial',status:'ready',environment_name:'owned-cluster',trial_cleanup_state:'scheduled',trial_expires_at:new Date(time+1800000).toISOString(),trial_cleanup_next_at:new Date(time+1800000).toISOString(),trial_cleanup_attempts:0}];
  const rpc=vi.fn(async()=>({data:{id:'one',trial_expires_at:rows[0].trial_expires_at},error:null}));
  const admin={database:{rpc,from(){const q={select:()=>q,eq:()=>q,neq:()=>q,order:()=>q,limit:async()=>({data:rows.filter(row=>row.trial_cleanup_state!=='complete'),error:null})};return q;}}};
  const save=vi.fn(async(id,changes)=>Object.assign(rows.find(row=>row.id===id),changes));
  const run=vi.fn(async()=>{});
  const options={admin,save,run,isBusy:()=>busy,now:()=>clock,...overrides};
  return {rows,rpc,save,run,options,manager:createTrials(options),advance:ms=>clock+=ms,setBusy:value=>busy=value};
}
it('reserves through the atomic database RPC using operator capacity',async()=>{const f=fixture();await f.manager.reserve({id:'one',user_id:'owner'});expect(f.rpc).toHaveBeenCalledWith('ebkit_reserve_trial',{p_project:{id:'one',user_id:'owner'},p_capacity:2});});
it.each([['TRIAL_USED',403],['CAPACITY_FULL',429],['Offline',503]])('fails closed on %s before a launch',async(message,status)=>{const f=fixture();f.rpc.mockResolvedValue({error:{message}});await expect(f.manager.reserve({})).rejects.toMatchObject({status});expect(f.run).not.toHaveBeenCalled();});
it('blocks expired updates and extra replicas but permits inspection and cleanup',()=>{const f=fixture(),project=f.rows[0];f.manager.guard(project,'deploy',{});expect(()=>f.manager.guard(project,'scale',{replicas:2})).toThrow('one replica');f.advance(1800000);for(const op of ['deploy','rollback','scale'])expect(()=>f.manager.guard(project,op,{replicas:1})).toThrow('expired');for(const op of ['status','logs','destroy','cleanup-status'])expect(()=>f.manager.guard(project,op,{})).not.toThrow();});
it('waits until expiry then durably schedules exactly one owned destruction',async()=>{const f=fixture();await f.manager.tick();expect(f.run).not.toHaveBeenCalled();f.advance(1800000);await Promise.all([f.manager.tick(),f.manager.tick()]);expect(f.run).toHaveBeenCalledOnce();expect(f.run.mock.calls[0]).toMatchObject(['destroy',{id:'one'},{confirmation:'owned-cluster'}]);expect(f.rows[0].trial_cleanup_state).toBe('destroying');expect(f.rows[0].trial_cleanup_attempts).toBe(1);});
it('reconstructs overdue work from persistent records after a restart',async()=>{const f=fixture();f.advance(1800001);const restarted=createTrials(f.options);await restarted.tick();expect(f.run).toHaveBeenCalledWith('destroy',expect.objectContaining({id:'one'}),{confirmation:'owned-cluster'});});
it('backs off failed cleanup launches and retries after restart',async()=>{const f=fixture();f.advance(1800000);f.run.mockRejectedValueOnce(new Error('spawn failed'));await f.manager.tick();expect(f.rows[0].trial_cleanup_state).toBe('pending');await createTrials(f.options).tick();expect(f.run).toHaveBeenCalledTimes(1);f.advance(60000);await createTrials(f.options).tick();expect(f.run).toHaveBeenCalledTimes(2);expect(f.rows[0].trial_cleanup_attempts).toBe(2);});
it('verifies managed cleanup instead of repeatedly destroying a terminated environment',async()=>{const f=fixture();Object.assign(f.rows[0],{status:'destroyed',trial_cleanup_state:'verifying',trial_cleanup_next_at:new Date(time-1).toISOString()});await f.manager.tick();expect(f.run.mock.calls[0][0]).toBe('cleanup-status');});
it('does not duplicate a running cloud operation or completed cleanup',async()=>{const f=fixture();f.advance(1800000);f.setBusy(true);await f.manager.tick();expect(f.run).not.toHaveBeenCalled();f.setBusy(false);f.rows[0].trial_cleanup_state='complete';await f.manager.tick();expect(f.run).not.toHaveBeenCalled();});
it('fails closed if persistent cleanup records are unavailable',async()=>{const f=fixture();f.options.admin.database.from=()=>{const q={select:()=>q,eq:()=>q,neq:()=>q,order:()=>q,limit:async()=>({error:{message:'offline'}})};return q;};await expect(f.manager.tick()).rejects.toMatchObject({status:503});expect(f.run).not.toHaveBeenCalled();});
it('starts the countdown through the idempotent server-side function, never a client date',async()=>{const f=fixture();await f.manager.start('one');expect(f.rpc).toHaveBeenCalledWith('ebkit_start_trial',{p_id:'one'});});
