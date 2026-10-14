import {useState} from 'react';
import {type Project,message} from './lib';
const actions=['metrics','cost','leftovers','pipeline','database-status','database-check','database-create','database-network','database-attach','database-migrate','database-snapshot','database-delete','autoscale','alarm','budget'];
const readActions=['metrics','cost','leftovers','pipeline','database-status','database-check'];
const guides:Record<string,{label:string;help:string;example:Record<string,unknown>}>= {
 metrics:{label:'View monitoring history',help:'Read CPU, memory, replica and available traffic history. Missing samples mean no data was reported.',example:{hours:24}},
 cost:{label:'View running costs',help:'Read tagged AWS costs and forecasts. Recent or shared charges may be missing.',example:{days:7}},
 leftovers:{label:'Find retained resources',help:'List tagged resources that may still exist or cost money. This does not delete anything.',example:{}},
 pipeline:{label:'Create a GitHub deployment workflow',help:'Generate workflow files with tests and AWS OIDC. Configure the role and required reviewers, then commit the downloaded files.',example:{roleArn:'',releasePolicy:{testCommands:[['npm','ci'],['npm','test']],healthPath:'/health',autoRollback:true}}},
 'database-status':{label:'View database status',help:'Check whether the managed PostgreSQL database is ready.',example:{}},
 'database-check':{label:'Test database connection',help:'Run SELECT 1. The backend needs a private network route to the database.',example:{}},
 'database-create':{label:'Create a PostgreSQL database',help:'Create a private, encrypted database. Enter real VPC/subnet/security-group IDs. It remains after the app is deleted.',example:{vpcId:'',subnetIds:[],applicationSecurityGroup:'',instanceClass:'db.t4g.micro',storageGb:20,multiAz:false}},
 'database-network':{label:'Allow the app to reach the database',help:'Grant PostgreSQL access from the application security group in the same VPC.',example:{applicationSecurityGroup:''}},
 'database-attach':{label:'Add the database connection to the app',help:'Merge DATABASE_URL into app settings. Apps using another variable name need that setting configured separately.',example:{}},
 'database-migrate':{label:'Apply database schema changes',help:'Run reviewed SQL once, tracked by migration ID and checksum. Application rollback does not undo schema changes.',example:{migrations:[{id:'001',sql:''}]}},
 'database-snapshot':{label:'Back up the database',help:'Create an RDS snapshot. Snapshots can remain billable after the database is removed.',example:{}},
 'database-delete':{label:'Delete the database',help:'Delete the managed database after exact identifier confirmation. A final snapshot and backups are retained.',example:{databaseConfirmation:''}},
 autoscale:{label:'Set automatic scaling rules',help:'Set replica bounds and CPU/memory targets. Extra replicas increase running costs.',example:{minimum:1,maximum:2,cpuPercent:75,memoryPercent:80}},
 alarm:{label:'Create a monitoring alert',help:'Create a CloudWatch alarm. Enter an existing SNS topic ARN and confirm its subscriptions to receive notifications.',example:{metric:'errors',threshold:5,topicArn:''}},
 budget:{label:'Create a monthly budget alert',help:'Email an alert at 80% of the tagged monthly budget. This does not stop AWS spending.',example:{monthlyUsd:10,email:''}},
};
export default function OperationsPanel({project,privileged,busy,profile,onAction}:{project:Project;privileged:boolean;busy:boolean;profile?:string;onAction:(project:Project,operation:string,body?:Record<string,unknown>)=>Promise<void>}){
 const [action,setAction]=useState('metrics'),[configuration,setConfiguration]=useState('{"hours":24}'),[confirmation,setConfirmation]=useState(''),[error,setError]=useState('');
 let report:any=null;try{if(project.last_operation==='production')report=JSON.parse(project.operation_log??'');}catch{}
 function download(name:string,value:unknown){const blob=new Blob([typeof value==='string'?value:JSON.stringify(value,null,2)],{type:'text/plain'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 async function run(){try{setError('');const parsed=JSON.parse(configuration);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Enter one JSON object for these settings.');await onAction(project,'production',{action,configuration:parsed,confirmation,...(profile?{profile}:{})});}catch(problem){setError(message(problem));}}
 return <section aria-label="Production operations"><h3>Production operations</h3><p className="field-help">Tools for your database, monitoring, scaling, deployment workflow and costs. Choose a task below.</p>
 <label>Production operation<select value={action} onChange={event=>{const next=event.target.value;setAction(next);setConfiguration(JSON.stringify(guides[next].example,null,2));setConfirmation('');setError('');}}>{actions.filter(value=>privileged||readActions.includes(value)).map(value=><option key={value} value={value}>{guides[value].label}</option>)}</select></label>
 <p className="field-help">{guides[action].help}</p>
 <label>Reviewed operation JSON<textarea rows={7} value={configuration} onChange={event=>setConfiguration(event.target.value)}/></label><label>Confirm production changes<input placeholder={project.environment_name} value={confirmation} onChange={event=>setConfirmation(event.target.value)}/></label>
 <button className="button" disabled={busy||project.operation_running||(!readActions.includes(action)&&(confirmation!==project.environment_name||project.status!=='ready'))} onClick={()=>void run()}>Run production operation</button>
 {error&&<p role="alert" className="form-error">{error}</p>}
 {report?.series?.map((series:any)=>{const points=series.points??[],values=points.map((point:any)=>point.value),max=Math.max(...values,1),min=Math.min(...values,0);return <figure key={series.id}><figcaption>{series.label}</figcaption>{points.length?<svg viewBox="0 0 600 120" role="img" aria-label={`${series.label} history`}><polyline fill="none" stroke="currentColor" strokeWidth="2" points={points.map((point:any,index:number)=>`${index*600/Math.max(points.length-1,1)},${110-(point.value-min)*100/(max-min||1)}`).join(' ')}/></svg>:<p>No samples available.</p>}</figure>;})}
 {report?.workflow&&<div className="detail-buttons"><button className="button" onClick={()=>download('ebkit.yml',report.workflow)}>Download GitHub workflow</button><button className="button" onClick={()=>download('release.json',report.releasePolicy)}>Download release policy</button><button className="button" onClick={()=>download('oidc-trust.json',report.oidcTrustPolicy)}>Download OIDC trust policy</button></div>}
 {!report&&project.last_operation==='production'&&<pre className="control-output">{project.operation_log}</pre>}
 {report&&<pre className="control-output">{JSON.stringify(report,null,2)}</pre>}
 </section>;
}
