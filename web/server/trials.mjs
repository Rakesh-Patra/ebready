export const trialColumns='id,user_id,name,repository,region,port,mode,status,application_name,environment_name,replicas,revision,plan,trial_reserved_at,trial_started_at,trial_expires_at,trial_cleanup_state,trial_cleanup_next_at,trial_cleanup_attempts';
const fail=(status,message)=>Object.assign(new Error(message),{status});
export function createTrials({admin,save,run,isBusy,now=Date.now,capacity=2}){
  let ticking=false;
  async function reserve(project){
    const {data,error}=await admin.database.rpc('ebkit_reserve_trial',{p_project:project,p_capacity:capacity});
    if(error){const message=JSON.stringify(error);if(message.includes('TRIAL_USED'))throw fail(403,'Your one free trial has already been used. The paid pass is coming soon.');if(message.includes('CAPACITY_FULL'))throw fail(429,'All trial slots are occupied or awaiting cleanup. Try again later.');throw fail(503,'Could not reserve a trial. Deployment was not started.');}
    if(!data?.id||!data.trial_expires_at)throw fail(503,'Trial reservation could not be verified.');
    return data;
  }
  async function start(id){const {data,error}=await admin.database.rpc('ebkit_start_trial',{p_id:id});if(error||!data?.trial_expires_at)throw fail(503,'Could not save the trial deadline.');return data;}
  function guard(project,operation,body){
    if(project.plan!=='free_trial')return;
    if(operation==='scale'&&body.replicas!==1)throw fail(403,'Free trials support one replica.');
    if(['deploy','rollback','scale'].includes(operation)&&(!project.trial_expires_at||now()>=Date.parse(project.trial_expires_at)||project.trial_cleanup_state!=='scheduled'))throw fail(410,'This trial has expired or cleanup has started. New builds and updates are blocked.');
  }
  async function tick(){
    if(ticking||isBusy())return;
    ticking=true;
    try{
      const {data,error}=await admin.database.from('ebkit_web_projects').select(trialColumns).eq('plan','free_trial').neq('trial_cleanup_state','complete').order('trial_cleanup_next_at',{ascending:true}).limit(101);
      if(error||!data||data.length>100)throw fail(503,'Trial cleanup records could not be loaded.');
      const project=data.find(item=>Date.parse(item.trial_cleanup_next_at??item.trial_expires_at)<=now());
      if(!project||isBusy())return;
      const attempts=(project.trial_cleanup_attempts??0)+1;
      const verifying=project.trial_cleanup_state==='verifying';
      await save(project.id,{trial_cleanup_state:verifying?'verifying':'destroying',trial_cleanup_attempts:attempts,trial_cleanup_next_at:new Date(now()+Math.min(1800000,60000*2**Math.min(attempts-1,5))).toISOString()});
      try{await run(verifying?'cleanup-status':'destroy',project,verifying?{}:{confirmation:project.environment_name});}
      catch{await save(project.id,{trial_cleanup_state:verifying?'verifying':'pending',last_error:'Automatic cleanup could not start. It will retry; resources may still incur charges.'});}
    }finally{ticking=false;}
  }
  async function account(user){
    const {data,error}=await admin.database.from('ebkit_web_trial_claims').select('project_id,claimed_at').eq('user_id',user.id).limit(1);
    if(error)throw fail(503,'Could not check trial eligibility.');
    return {approved:true,trialUsed:data.length>0,durationMinutes:30,capacity};
  }
  return {reserve,start,guard,tick,account};
}
