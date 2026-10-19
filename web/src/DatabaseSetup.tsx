import {useState} from 'react';
import {parseEnv,quoteEnvValues,message} from './lib';

export const databaseVariable=(name:string)=>/^(DATABASE_URL|POSTGRES_URL|POSTGRESQL_URL|DB_URL|MYSQL_URL|MONGODB_URI|MONGO_URI|REDIS_URL)$/.test(name);
export const postgresVariable=(name:string)=>/^(DATABASE_URL|POSTGRES_URL|POSTGRESQL_URL)$/.test(name);
export function databaseProblem(values:Record<string,string>){
 for(const [name,value] of Object.entries(values)){
  if(!databaseVariable(name)||!value.trim())continue;
  try{const url=new URL(value);if(!['postgres:','postgresql:','mysql:','mongodb:','mongodb+srv:','redis:','rediss:'].includes(url.protocol)||!url.hostname)return `${name}: use an external database connection URL supported by your app.`;if(/^(localhost|.*\.localhost|127\..*|0\.0\.0\.0|\[::1\])$/i.test(url.hostname))return `${name}: localhost refers to the deployed container. Use a database reachable from AWS.`;}
  catch{return `${name}: enter a valid connection URL. Its value is hidden.`;}
 }
 return '';
}
type Props={names:string[];required:boolean;text:string;onText:(value:string)=>void;mode:'existing'|'managed';onMode:(value:'existing'|'managed')=>void;managedAllowed:boolean;confirmed:boolean;onConfirm:(value:boolean)=>void;replacement:boolean};
export default function DatabaseSetup({names,required,text,onText,mode,onMode,managedAllowed,confirmed,onConfirm,replacement}:Props){
 const [error,setError]=useState('');
 let values:Record<string,string>={};try{values=parseEnv(text);}catch{}
 function edit(name:string,value:string){try{setError('');const next={...parseEnv(text),[name]:value};onText(quoteEnvValues(Object.entries(next).map(([key,item])=>`${key}=${item}`).join('\n')));}catch(problem){setError(message(problem));}}
 function choose(value:'existing'|'managed'){if(value==='managed'){try{const next=parseEnv(text);for(const name of names)delete next[name];onText(quoteEnvValues(Object.entries(next).map(([key,item])=>`${key}=${item}`).join('\n')));}catch(problem){setError(message(problem));return;}}onMode(value);onConfirm(false);}
 const managedSupported=names.every(postgresVariable);
 const content=<fieldset className="database-setup"><legend>Your app's database</legend><p className="field-help">{required?'Your source requires a database connection before deployment.':'No required database URL was detected. Leave this empty if your app does not need a database.'} This is separate from EBKit's own InsForge database.</p>
  <label>Database option<select value={mode} onChange={event=>choose(event.target.value as 'existing'|'managed')}><option value="existing">Use my existing database</option><option value="managed" disabled={!managedAllowed||!managedSupported||replacement}>Create AWS PostgreSQL before deployment</option></select></label>
  {mode==='existing'?<><p className="field-help">Create a database with your provider, then paste its connection URL. EBKit passes the value to your app; it does not copy or migrate your data.</p>{names.map(name=><label key={name}>Connection URL ({name})<input type="password" autoComplete="off" spellCheck={false} value={values[name]??''} placeholder="Paste the URL from your database provider" onChange={event=>edit(name,event.target.value)}/></label>)}<p className="field-help">Use a database reachable from AWS. localhost and Docker-only hostnames from your computer will not work.</p></>:<><p className="field-help">EBKit will build the app, create a private PostgreSQL database in the operator's configured AWS network, wait until it is available, then provide {names.join(', ')} before starting the app. No URL needs to be pasted. Your app must support PostgreSQL.</p><label className="checkbox"><input type="checkbox" checked={confirmed} onChange={event=>onConfirm(event.target.checked)}/>I approve separate RDS charges. The database survives app deletion and must be deleted separately.</label><p className="field-help">Database schemas and migrations remain your responsibility. Check deployment logs for progress; provisioning can take several minutes.</p></>}
  {(!managedAllowed||!managedSupported||replacement)&&<p className="field-help">{replacement?'Existing deployments reuse their saved database settings unless you supply replacement values.':!managedSupported?'AWS creation here supports PostgreSQL only. Supply an existing URL for other database engines.':'AWS creation requires operator-configured networking and approved access without a trial deadline. Short trials use an existing database URL.'}</p>}
  {error&&<p className="form-error" role="alert">{error}</p>}
 </fieldset>;
 return required?content:<details className="settings-group"><summary>Does your app need a database?</summary>{content}</details>;
}
