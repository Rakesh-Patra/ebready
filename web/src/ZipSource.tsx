import { useState } from 'react';
import { Download, FileArchive, ShieldCheck } from 'lucide-react';
import { backend, uploadZip, downloadPreparedZip, message } from './lib';

export type ScoutReport={gate_passed:boolean;bypassed?:boolean;available?:boolean;summary:string;gate_reason?:string;critical_count?:number;high_count?:number;details:Array<{id?:string;severity?:string;packages?:string[];paths?:string[];fixed_version?:string}>};
export type PreparedZip = {uploadId:string;name:string;dockerfile:string;reviewHash:string;generated:boolean;generator?:'gordon'|'gemini'|'existing'|'unknown';port:number;environment:Array<{name:string;required:boolean;source:string}>;environmentScanComplete:boolean;scout?:ScoutReport};
export function downloadFile(data:Blob,name:string) {
  const url=URL.createObjectURL(data),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export default function ZipSource({prepared,onPrepared,reviewed,onReviewed,signedIn,login,port,onPort}:{prepared:PreparedZip|null;onPrepared:(value:PreparedZip|null)=>void;reviewed:boolean;onReviewed:(value:boolean)=>void;signedIn:boolean;login:()=>void;port:number;onPort:(value:number)=>void}) {
  const [file,setFile]=useState<File|null>(null),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[uploaded,setUploaded]=useState<{uploadId:string;port?:number}|null>(null),[portReviewed,setPortReviewed]=useState(false),[bypassAcknowledged,setBypassAcknowledged]=useState(false);
  async function inspectZip() {
    if(!signedIn){login();return;}if(!file)return;
    setBusy(true);setError('');
    try{const result=await uploadZip(file);setUploaded(result);onPort(result.port??8080);setPortReviewed(false);}catch(problem){setError(message(problem));}finally{setBusy(false);}
  }
  async function prepare() {
    if(!signedIn){login();return;}if(!uploaded||!portReviewed||!Number.isInteger(port)||port<1||port>65535)return;
    setBusy(true);setError('');
    try{
      const draft=await backend<PreparedZip>(`/api/uploads/${uploaded.uploadId}/prepare`,{port,allowAI:consent});
      onPrepared(draft);onReviewed(false);
    }catch(problem){setError(message(problem));}finally{setBusy(false);}
  }
  async function downloadZip() {
    if(!prepared)return;setBusy(true);setError('');
    try{downloadFile(await downloadPreparedZip(prepared.uploadId,prepared.reviewHash),prepared.name.replace(/\.zip$/i,'')+'-prepared.zip');}catch(problem){setError(message(problem));}finally{setBusy(false);}
  }
  async function bypassScout(){
    if(!prepared||!bypassAcknowledged)return;setBusy(true);setError('');
    try{onPrepared(await backend<PreparedZip>(`/api/uploads/${prepared.uploadId}/prepare`,{port:prepared.port,skipScout:true,acknowledgeScoutBypass:true}));onReviewed(false);}catch(problem){setError(message(problem));}finally{setBusy(false);}
  }
  return <div className="zip-source">
    <label className="zip-upload"><FileArchive size={24}/><strong>Upload your project ZIP</strong><span>25 MB maximum · omit .env, credentials and dependencies</span><input aria-label="Project ZIP file" type="file" accept=".zip,application/zip" disabled={busy} onChange={event=>{const selected=event.target.files?.[0];onPrepared(null);onReviewed(false);setUploaded(null);setPortReviewed(false);setBypassAcknowledged(false);setError('');if(selected&&(!selected.name.toLowerCase().endsWith('.zip')||selected.size>25*1024*1024)){setFile(null);setError('Choose a ZIP no larger than 25 MB.');return;}setFile(selected??null);}}/>{file&&<small>{file.name}</small>}</label>
    {!prepared&&<>{file&&!uploaded&&<button className="button primary" disabled={busy} onClick={()=>void inspectZip()}>{busy?'Inspecting ZIP...':signedIn?'Detect project settings':'Log in to inspect ZIP'}</button>}{uploaded&&<><label>Container port for ZIP<input type="number" min="1" max="65535" disabled={busy} value={port} onChange={event=>{setPortReviewed(false);onPort(Number(event.target.value));}}/></label><p className="field-help">Confirm the detected port.</p><label className="checkbox"><input type="checkbox" checked={portReviewed} onChange={event=>setPortReviewed(event.target.checked)}/>I checked the container port.</label><label className="checkbox"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/>Allow AI to read metadata and manifests to create a missing Dockerfile.</label><details className="inline-help"><summary>Preparation details</summary><p>Gordon creates the Dockerfile, with Gemini fallback using the operator key. Scout checks source dependencies. No image is built or scanned.</p></details>{portReviewed&&consent&&<button className="button primary" disabled={busy||!Number.isInteger(port)||port<1||port>65535} onClick={()=>void prepare()}>{busy?'Preparing & scanning...':'Prepare & review Dockerfile'}</button>}</>}</>}

    {prepared&&<div className="dockerfile-review"><div className="section-heading"><strong>{prepared.generated?prepared.generator==='gordon'?'Docker AI (Gordon) draft':prepared.generator==='gemini'?'Gemini draft':'AI Dockerfile draft':'Existing root Dockerfile'}</strong><button className="button small" onClick={()=>downloadFile(new Blob([prepared.dockerfile],{type:'text/plain'}),'Dockerfile')}><Download size={14}/>Dockerfile</button></div><pre>{prepared.dockerfile}</pre><p className="field-help">Review the Dockerfile. Image not built or scanned.</p><div className="notice"><ShieldCheck size={19}/><p>{prepared.scout?.bypassed?'SECURITY NOT VERIFIED — Docker Scout was explicitly bypassed. CVEs may remain.':prepared.scout?.gate_passed?'Source Scout passed.':prepared.scout?.summary||'Source security is not verified. Prepare this ZIP again.'}</p></div>{prepared.scout&&<details><summary>Docker Scout report</summary><pre>{[prepared.scout.summary,prepared.scout.gate_reason,...prepared.scout.details.map(item=>`${item.id||'Unknown CVE'} [${item.severity||'unknown'}]\nPackage: ${(item.packages||[]).join(', ')}\nLocation: ${(item.paths||[]).join(', ')}\nFixed version: ${item.fixed_version||'not reported'}`)].filter(Boolean).join('\n\n')}</pre></details>}{prepared.scout&&!prepared.scout.gate_passed&&!prepared.scout.bypassed&&<><p className="form-error">Fix the reported dependencies and upload a new ZIP. Deployment is blocked.</p><label className="checkbox"><input type="checkbox" checked={bypassAcknowledged} onChange={event=>setBypassAcknowledged(event.target.checked)}/>I explicitly choose to bypass Scout. Security will NOT be verified.</label>{bypassAcknowledged&&<button className="button danger" disabled={busy} onClick={()=>void bypassScout()}>Prepare with Scout bypass</button>}</>}<label className="checkbox"><input type="checkbox" checked={reviewed} onChange={event=>onReviewed(event.target.checked)}/>I reviewed this root Dockerfile.</label>{reviewed&&<button className="button" disabled={busy} onClick={()=>void downloadZip()}><Download size={15}/>Download prepared ZIP</button>}<p className="field-help">Add environment values in the next step.</p></div>}
    {error&&<p className="form-error" role="alert">{error}</p>}
  </div>;
}
