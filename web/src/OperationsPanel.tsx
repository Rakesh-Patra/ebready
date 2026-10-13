import {useState} from 'react';
import {type Project,message} from './lib';
const actions=['metrics','cost','leftovers','pipeline','database-status','database-check','database-create','database-network','database-attach','database-migrate','database-snapshot','database-delete','autoscale','alarm','budget'];
export default function OperationsPanel({project,privileged,busy,profile,onAction}:{project:Project;privileged:boolean;busy:boolean;profile?:string;onAction:(project:Project,operation:string,body?:Record<string,unknown>)=>Promise<void>}){
 const [action,setAction]=useState('metrics'),[configuration,setConfiguration]=useState('{"hours":24}'),[confirmation,setConfirmation]=useState(''),[error,setError]=useState('');
 let report:any=null;try{if(project.last_operation==='production')report=JSON.parse(project.operation_log??'');}catch{}
 function download(name:string,value:unknown){const blob=new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type:'text/plain'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 return <section aria-label="Production operations"><h3>Production operations</h3><p className="field-help">Real AWS integrations. Private databases require VPC connectivity for checks and migrations. Persistent resources are retained beyond environment deletion.</p>
 <label>Production operation<select value={action} onChange={event=>setAction(event.target.value)}>{actions.filter(value=>privileged||['metrics','cost','leftovers','pipeline','database-status','database-check'].includes(value)).map(value=><option key={value}>{value}</option>)}</select></label>
 <label>Reviewed operation JSON<textarea rows={7} value={configuration} onChange={event=>setConfiguration(event.target.value)}/></label><label>Confirm production changes<input placeholder={project.environment_name} value={confirmation} onChange={event=>setConfirmation(event.target.value)}/></label>
 <button className="button" disabled={busy||project.operation_running} onClick={()=>{try{setError('');void onAction(project,'production',{action,configuration:JSON.parse(configuration),confirmation,...(profile?{profile}:{})});}catch(problem){setError(message(problem));}}}>Run production operation</button>
 {error&&<p role="alert" className="form-error">{error}</p>}
 {report?.series?.map((series:any)=>{const points=series.points??[],values=points.map((point:any)=>point.value),max=Math.max(...values,1),min=Math.min(...values,0);return <figure key={series.id}><figcaption>{series.label}</figcaption>{points.length?<svg viewBox="0 0 600 120" role="img" aria-label={`${series.label} history`}><polyline fill="none" stroke="currentColor" strokeWidth="2" points={points.map((point:any,index:number)=>`${index*600/Math.max(points.length-1,1)},${110-(point.value-min)*100/(max-min||1)}`).join(' ')}/></svg>:<p>No samples available.</p>}</figure>;})}
 {report?.workflow&&<div className="detail-buttons"><button className="button" onClick={()=>download('ebkit.yml',report.workflow)}>Download GitHub workflow</button><button className="button" onClick={()=>download('release.json',report.releasePolicy)}>Download release policy</button><button className="button" onClick={()=>download('oidc-trust.json',report.oidcTrustPolicy)}>Download OIDC trust policy</button></div>}
 {!report&&project.last_operation==='production'&&<pre className="control-output">{project.operation_log}</pre>}
 {report&&<pre className="control-output">{JSON.stringify(report,null,2)}</pre>}
 </section>;
}
