import {useEffect,useState,useRef} from 'react';
import {backend,message,parseEnv,type Project} from './lib';
import OperationsPanel from './OperationsPanel';
import {trialExpired} from './TrialCountdown';

type Props={project:Project;projects:Project[];privileged:boolean;busy:boolean;onAction:(project:Project,operation:string,body?:Record<string,unknown>)=>Promise<void>};
export default function CloudControls({project,projects,privileged,busy,onAction}:Props){
 const actionRef=useRef(onAction);actionRef.current=onAction;
 const [profiles,setProfiles]=useState<string[]>([]),[profile,setProfile]=useState(''),[variables,setVariables]=useState(''),[unset,setUnset]=useState(''),[options,setOptions]=useState(''),[removeOptions,setRemoveOptions]=useState(''),[template,setTemplate]=useState(''),[version,setVersion]=useState(''),[cloneName,setCloneName]=useState(''),[destination,setDestination]=useState(''),[confirmation,setConfirmation]=useState(''),[costs,setCosts]=useState(false),[error,setError]=useState(''),[refresh,setRefresh]=useState(''),[release,setRelease]=useState('{"testCommands":[["npm","ci"],["npm","test"]],"healthPath":"/health","autoRollback":true}');
 useEffect(()=>{let active=true;backend<{profiles:string[]}>('/api/profiles',{}).then(result=>{if(active)setProfiles(result?.profiles??[]);}).catch(()=>{});return()=>{active=false;};},[]);
 useEffect(()=>{if(!refresh)return;const timer=setInterval(()=>{if(!busy&&!project.operation_running)void actionRef.current(project,refresh,profile?{profile}:{});},30000);return()=>clearInterval(timer);},[refresh,busy,project.operation_running,project.id,profile]);
 const blocked=busy||!!project.operation_running,mutationsBlocked=blocked||trialExpired(project)||project.status!=='ready';
 const confirmed=confirmation===project.environment_name;
 const destinations=projects.filter(item=>item.id!==project.id&&item.mode==='live'&&item.application_name===project.application_name&&item.region===project.region&&item.status==='ready'&&!trialExpired(item));
 async function act(operation:string,body:Record<string,unknown>={}){
  setError('');try{await onAction(project,operation,{...body,...(profile?{profile}:{}),confirmation});}catch(problem){setError(message(problem));}
 }
 return <section className="cloud-controls" aria-label="Cloud controls">
  <h3>Check your app</h3><p className="field-help">Check health or recent activity. Open App variables only when you need to change a setting.</p>

  <div className="detail-buttons"><button className="button" disabled={blocked} onClick={()=>void act('health')}>Check app health</button><button className="button" disabled={blocked} onClick={()=>void act('events')}>View recent activity</button></div>
  <details className="settings-group"><summary>App variables</summary><p className="field-help">Settings your app reads, such as an API key or database address. Enter one KEY=VALUE per line. To save changes, confirm your environment name below.</p>
   <label>Add or update variables (KEY=VALUE)<textarea value={variables} onChange={event=>setVariables(event.target.value)} placeholder="NEW_SETTING=value"/></label>
   <label>Remove variables (names only)<input value={unset} onChange={event=>setUnset(event.target.value)} placeholder="OLD_SETTING, UNUSED_SETTING"/></label>
  <label>Confirm changes by typing the environment name<input value={confirmation} placeholder={project.environment_name} onChange={event=>setConfirmation(event.target.value)}/></label>
   <button className="button" disabled={mutationsBlocked||!confirmed||(!variables.trim()&&!unset.trim())} onClick={()=>{try{void act('config',{environment:parseEnv(variables),unset:unset.split(/[\s,]+/).filter(Boolean)});setVariables('');}catch(problem){setError(message(problem));}}}>Apply variable changes</button>
   <p className="field-help">Existing settings are preserved. Values are sent to AWS and are not stored in the project database.</p>
  </details>
  <details className="settings-group advanced-settings"><summary>Advanced AWS settings</summary><p className="field-help">Optional tools for experienced users. Your everyday settings above are available to everyone.</p><details className="settings-group"><summary>Monitoring &amp; technical output</summary>  <label>Refresh monitoring<select value={refresh} onChange={event=>setRefresh(event.target.value)}><option value="">Off</option><option value="health">Health every 30 seconds</option><option value="events">Events every 30 seconds</option></select></label>
  {project.last_operation&&['health','events','options','config'].includes(project.last_operation)&&project.operation_log&&<pre className="control-output">{project.operation_log}</pre>}
<button className="button" disabled={blocked} onClick={()=>void act('config')}>Refresh variable names</button></details><label>Confirm advanced changes by typing the environment name<input value={confirmation} placeholder={project.environment_name} onChange={event=>setConfirmation(event.target.value)}/></label><p className="field-help">For experienced users: deployment rules, databases, and AWS infrastructure. Some actions create extra charges or change live traffic.</p>  {profiles.length>0&&<label>AWS credentials profile<select value={profile} onChange={event=>setProfile(event.target.value)}><option value="">Operator default</option>{profiles.map(value=><option key={value}>{value}</option>)}</select></label>}<button className="button" disabled={blocked} onClick={()=>void act('options')}>View AWS configuration</button><details className="settings-group"><summary>Infrastructure tools: databases, spending &amp; automation</summary><OperationsPanel project={project} privileged={privileged} busy={busy} profile={profile} onAction={onAction}/></details><details><summary>Deploy with tests &amp; automatic recovery</summary><label>Reviewed release policy JSON<textarea value={release} onChange={event=>setRelease(event.target.value)}/></label><p className="field-help">Tests run in CodeBuild before deployment. HTTP checks can restore the previous version; database schema changes are never rolled back automatically.</p><button className="button" disabled={mutationsBlocked||!confirmed} onClick={()=>{try{void act('deploy',{confirmCosts:true,releasePolicy:JSON.parse(release)});}catch(problem){setError(message(problem));}}}>Deploy with tests and recovery</button></details>
  {privileged&&<>
   <details><summary>AWS configuration & saved templates</summary>
    <label>Options JSON<textarea value={options} onChange={event=>setOptions(event.target.value)} placeholder={'[{"Namespace":"aws:elasticbeanstalk:eks:environment:startup-probe","OptionName":"initial-delay-seconds","Value":"20"}]'}/></label>
    <label>Options to remove<input value={removeOptions} onChange={event=>setRemoveOptions(event.target.value)} placeholder="namespace/option, namespace/option"/></label>
    <button className="button" disabled={mutationsBlocked||!confirmed||(!options.trim()&&!removeOptions.trim())} onClick={()=>{try{void act('options',{...(options.trim()?{options:JSON.parse(options)}:{}),removeOptions:removeOptions.split(/[\s,]+/).filter(Boolean)});setOptions('');}catch(problem){setError(message(problem));}}}>Apply environment options</button>
    <label>Saved configuration name<input value={template} onChange={event=>setTemplate(event.target.value)}/></label>
    <p className="field-help">AWS saved configuration templates work with Standard environments only. Cluster environments use the options editor above.</p>
    <div className="detail-buttons"><button className="button" disabled={mutationsBlocked||!confirmed||!template.trim()} onClick={()=>void act('config-save',{templateName:template})}>Save configuration</button><button className="button" disabled={mutationsBlocked||!confirmed||!template.trim()} onClick={()=>void act('config-apply',{templateName:template})}>Apply saved configuration</button></div>
   </details>
   <details><summary>Delete an unused deployment version</summary><label>Version to delete<input value={version} onChange={event=>setVersion(event.target.value)}/></label><p className="field-help">AWS versions deployed in any active environment cannot be deleted. Container images are retained.</p><button className="button danger" disabled={mutationsBlocked||!confirmed||!version.trim()} onClick={()=>void act('version-delete',{version})}>Delete unused version</button></details>
   <details><summary>Copy app or switch live traffic</summary><label>Clone project name<input value={cloneName} onChange={event=>setCloneName(event.target.value)}/></label><label className="checkbox"><input type="checkbox" checked={costs} onChange={event=>setCosts(event.target.checked)}/>I understand the clone creates additional billable AWS resources.</label><button className="button" disabled={mutationsBlocked||!confirmed||!costs||!cloneName.trim()} onClick={()=>void act('clone',{name:cloneName,confirmCosts:costs})}>Clone environment</button>
    <label>Swap URL with<select value={destination} onChange={event=>setDestination(event.target.value)}><option value="">Choose a Ready project</option>{destinations.map(item=><option value={item.id} key={item.id}>{item.name}</option>)}</select></label><p className="field-help">Swapping URLs redirects traffic between these environments. Review both versions before switching.</p><button className="button" disabled={mutationsBlocked||!confirmed||!destination} onClick={()=>void act('swap',{destinationId:destination})}>Swap environment URLs</button>
   </details>
  </>}
  </details>
  {error&&<p className="form-error" role="alert">{error}</p>}
 </section>;
}
