export const trialColumns='id,user_id,name,repository,region,port,mode,status,application_name,environment_name,replicas,revision,plan,trial_reserved_at,trial_started_at,trial_expires_at,trial_cleanup_state,trial_cleanup_next_at,trial_cleanup_attempts';
export const isTimedPlan=plan=>plan==='free_trial'||plan==='paid_test';
const fail=(status,message)=>Object.assign(new Error(message),{status});
export function createTrials({admin,save,run,isBusy,now=Date.now,capacity=2,testingEmails=[],evaluatorEmails=[],paidTestEmails=[],allowedEmails=[],strictCapacity=false}){
  let ticking=false;
  async function reserveInternal(project,user,{paidTest=false}={}){
    const {operator_test:ignored,operator_evaluator:ignoredEvaluator,operator_paid_test:ignoredPaidTest,...safeProject}=project;
    if(user?.id&&user.id===project.user_id&&testingEmails.includes(user.email?.toLowerCase()))safeProject.operator_test=true;
    if(user?.id&&user.id===project.user_id&&evaluatorEmails.includes(user.email?.toLowerCase())){safeProject.operator_test=true;safeProject.operator_evaluator=true;}
    if(paidTest){
      if(user?.id!==project.user_id||!paidTestEmails.includes(user.email?.toLowerCase()))throw fail(403,'The two-hour test pass is not enabled for this account.');
      safeProject.operator_paid_test=true;
    }
    const {data,error}=await admin.database.rpc('ebkit_reserve_trial',{p_project:safeProject,p_capacity:capacity});
    if(error){const message=JSON.stringify(error);if(message.includes('TEST_CAPACITY_FULL'))throw fail(409,'Your 10 testing slots are occupied or awaiting cleanup. Wait for a slot to finish cleanup.');if(message.includes('PAID_TEST_ACTIVE'))throw fail(409,'Finish cleanup of your active test pass before creating another live deployment.');if(message.includes('TRIAL_USED'))throw fail(403,'Your one free trial has already been used.');if(message.includes('CAPACITY_FULL'))throw fail(429,'All trial slots are occupied or awaiting cleanup. Try again later.');throw fail(503,'Could not reserve a trial. Deployment was not started.');}
    if(!data?.id||(safeProject.operator_paid_test&&data.plan!=='paid_test')||(!safeProject.operator_evaluator&&!data.trial_expires_at)||(safeProject.operator_evaluator&&!safeProject.operator_paid_test&&(data.plan==='free_trial'||data.trial_expires_at)))throw fail(503,'Deployment reservation could not be verified.');
    return data;
  }
  let reserving=false;
  async function reserve(project,user,options={}){
    if(!strictCapacity)return reserveInternal(project,user,options);
    if(reserving)throw fail(409,'Another deployment is being reserved. Try again shortly.');
    reserving=true;
    try{
      const {data,error}=await admin.database.from('ebkit_web_projects').select('id,status,plan,trial_cleanup_state').eq('mode','live').limit(101);
      if(error||!data||data.length>100)throw fail(503,'Could not verify hosted trial capacity.');
      if(data.filter(p=>p.status!=='destroyed'||(isTimedPlan(p.plan)&&p.trial_cleanup_state!=='complete')).length>=capacity)throw fail(429,'The hosted trial slot is in use or awaiting cleanup.');
      return await reserveInternal(project,user,options);
    }finally{reserving=false;}
  }
  async function start(id){const {data,error}=await admin.database.rpc('ebkit_start_trial',{p_id:id});if(error||!data?.trial_expires_at)throw fail(503,'Could not save the trial deadline.');return data;}
  function guard(project,operation,body,user=null,existing=false){
    const testing=user?.id===project.user_id&&[...testingEmails,...evaluatorEmails].includes(user?.email?.toLowerCase());
    if(existing&&operation==='rollback'&&project.plan==='free_trial'&&!testing)throw fail(403,'Rollback is unavailable on free access.');
    if(!isTimedPlan(project.plan))return;
    if(project.plan==='paid_test'&&operation==='clone')throw fail(403,'The two-hour test pass covers one application.');
    if(operation==='scale'&&body.replicas!==1)throw fail(403,`${project.plan==='paid_test'?'The two-hour test pass':'Free trials'} support one replica.`);
    const productionWrite=operation==='production'&&!['metrics','cost','leftovers','database-status','database-check','pipeline'].includes(body.action);
    if((['deploy','rollback','scale','clone','swap','version-delete','config-save','config-apply'].includes(operation)||(operation==='config'&&(body.environment||body.unset?.length))||(operation==='options'&&(body.options||body.removeOptions?.length))||productionWrite)&&(!project.trial_expires_at||now()>=Date.parse(project.trial_expires_at)||project.trial_cleanup_state!=='scheduled'))throw fail(410,'This trial has expired or cleanup has started. New builds and updates are blocked.');
  }
  async function tick(){
    if(ticking||isBusy())return;
    ticking=true;
    try{
      const batches=await Promise.all(['free_trial','paid_test'].map(plan=>admin.database.from('ebkit_web_projects').select(trialColumns).eq('plan',plan).neq('trial_cleanup_state','complete').order('trial_cleanup_next_at',{ascending:true}).limit(101)));
      if(batches.some(({error,data})=>error||!data||data.length>100))throw fail(503,'Trial cleanup records could not be loaded.');
      const project=batches.flatMap(({data})=>data).filter(item=>Date.parse(item.trial_cleanup_next_at??item.trial_expires_at)<=now()).sort((a,b)=>Date.parse(a.trial_cleanup_next_at??a.trial_expires_at)-Date.parse(b.trial_cleanup_next_at??b.trial_expires_at))[0];
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
    const evaluator=evaluatorEmails.includes(user.email?.toLowerCase());
    const testing=evaluator||testingEmails.includes(user.email?.toLowerCase());
    const email=user.email?.toLowerCase();
    return {approved:true,trialUsed:!testing&&data.length>0,durationMinutes:evaluator?null:30,capacity,testing,paidTestAvailable:paidTestEmails.includes(email)&&allowedEmails.includes(email),paidTestDurationMinutes:120};
  }
  return {reserve,start,guard,tick,account};
}
