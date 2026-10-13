import { useState } from 'react';
import { Download, FileArchive, ShieldCheck, ShieldAlert } from 'lucide-react';
import { backend, uploadZip, downloadPreparedZip, message } from './lib';
import DoctorPanel from './DoctorPanel';

export type ScoutReport={gate_passed:boolean;bypassed?:boolean;available?:boolean;summary:string;gate_reason?:string;critical_count?:number;high_count?:number;details:Array<{id?:string;severity?:string;packages?:string[];paths?:string[];fixed_version?:string}>};
export type QualityReport={passed:boolean;runtimeImage:string;distroless:boolean;checks:Array<{name:string;passed:boolean;details:string;critical:boolean}>;warnings:string[];build:string;runtime:string;imageSecurity:string};
export type PreparedZip = {uploadId:string;name:string;dockerfile:string;reviewHash:string;generated:boolean;generator?:'gordon'|'gemini'|'recipe'|'existing'|'unknown';port:number;environment:Array<{name:string;required:boolean;source:string}>;environmentScanComplete:boolean;scout?:ScoutReport;quality?:QualityReport};
export function downloadFile(data:Blob,name:string) {
  const url=URL.createObjectURL(data),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export default function ZipSource({prepared,onPrepared,reviewed,onReviewed,signedIn,login,port,onPort,initialUpload}:{prepared:PreparedZip|null;onPrepared:(value:PreparedZip|null)=>void;reviewed:boolean;onReviewed:(value:boolean)=>void;signedIn:boolean;login:()=>void;port:number;onPort:(value:number)=>void;initialUpload?:{uploadId:string;port?:number}}) {
  const [file,setFile]=useState<File|null>(null),[consent,setConsent]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[uploaded,setUploaded]=useState<{uploadId:string;port?:number}|null>(initialUpload??null),[portReviewed,setPortReviewed]=useState(false),[bypassAcknowledged,setBypassAcknowledged]=useState(false),[dockerfileOpen,setDockerfileOpen]=useState(false);
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
    {!initialUpload&&<label className="zip-upload"><FileArchive size={24}/><strong>Upload your project ZIP</strong><span>25 MB maximum · omit .env, credentials and dependencies</span><input aria-label="Project ZIP file" type="file" accept=".zip,application/zip" disabled={busy} onChange={event=>{const selected=event.target.files?.[0];onPrepared(null);onReviewed(false);setUploaded(null);setPortReviewed(false);setBypassAcknowledged(false);setDockerfileOpen(false);setError('');if(selected&&(!selected.name.toLowerCase().endsWith('.zip')||selected.size>25*1024*1024)){setFile(null);setError('Choose a ZIP no larger than 25 MB.');return;}setFile(selected??null);}}/></label>}
    {!prepared&&<>{file&&!uploaded&&<button className="button primary" disabled={busy} onClick={()=>void inspectZip()}>{busy?'Inspecting ZIP...':signedIn?'Detect project settings':'Log in to inspect ZIP'}</button>}{uploaded&&<><label>Container port for ZIP<input type="number" min="1" max="65535" disabled={busy} value={port} onChange={event=>{setPortReviewed(false);onPort(Number(event.target.value));}}/></label><p className="field-help">Confirm the detected port.</p><label className="checkbox"><input type="checkbox" checked={portReviewed} onChange={event=>setPortReviewed(event.target.checked)}/>I checked the container port.</label><label className="checkbox"><input type="checkbox" checked={consent} onChange={event=>setConsent(event.target.checked)}/>Allow AI analysis to generate a production root Dockerfile if missing.</label><details className="inline-help"><summary>Preparation details</summary><p>Reviewed recipes create supported distroless Dockerfiles; Gordon and Gemini handle other projects behind a quality gate. Multi-tier projects use one repository-root Dockerfile; stateful services remain external. Scout checks source dependencies. No image is built or scanned.</p></details>{portReviewed&&consent&&<button className="button primary" disabled={busy||!Number.isInteger(port)||port<1||port>65535} onClick={()=>void prepare()}>{busy?'Preparing & scanning...':'Prepare & review Dockerfile'}</button>}</>}</>}

    {(uploaded||prepared)&&<DoctorPanel key={`${uploaded?.uploadId??prepared?.uploadId}-${port}`} uploadId={(uploaded?.uploadId??prepared?.uploadId)!} port={port} disabled={busy} onApplied={()=>{const id=uploaded?.uploadId??prepared?.uploadId;if(id)setUploaded({uploadId:id,port});onPrepared(null);onReviewed(false);setPortReviewed(false);setBypassAcknowledged(false);setDockerfileOpen(false);}}/>}

    {prepared&&<div className="dockerfile-review">
      <div className={`zip-security ${prepared.scout?.gate_passed?'passed':'needs-review'}`} role="status">
        {prepared.scout?.gate_passed?<ShieldCheck size={21}/>:<ShieldAlert size={21}/>}
        <div><strong>{prepared.scout?.bypassed?'Security check skipped':prepared.scout?.gate_passed?'Source security check passed':prepared.scout?.available===false?'Security check incomplete':'Security fixes needed'}</strong>
        {prepared.scout?.bypassed?<p>Security is NOT VERIFIED. Vulnerabilities may remain.</p>:!prepared.scout?.gate_passed?<p>{prepared.scout?.available===false?'Prepare the ZIP again to retry the check.':'Update vulnerable dependencies, then upload a new ZIP. Deployment is blocked.'}</p>:null}
        {!prepared.scout?.gate_passed&&!prepared.scout?.bypassed&&typeof prepared.scout?.critical_count==='number'&&<span className="security-counts">{prepared.scout.critical_count} Critical / {prepared.scout.high_count??0} High</span>}</div>
      </div>
      {!reviewed&&!dockerfileOpen&&<button className="button primary review-continue" onClick={()=>setDockerfileOpen(true)}>Review Dockerfile to continue</button>}
      <details className="review-section" open={dockerfileOpen} onToggle={event=>setDockerfileOpen(event.currentTarget.open)}><summary>View Dockerfile {reviewed&&<span>Reviewed</span>}</summary>
        <div className="section-heading"><strong>{prepared.generated?prepared.generator==='recipe'?'EBKit reviewed distroless recipe':prepared.generator==='gordon'?'Docker AI (Gordon) draft':prepared.generator==='gemini'?'Gemini draft':'AI Dockerfile draft':'Existing root Dockerfile'}</strong><button className="button small" onClick={()=>downloadFile(new Blob([prepared.dockerfile],{type:'text/plain'}),'Dockerfile')}><Download size={14}/>Dockerfile</button></div>
        <pre>{prepared.dockerfile}</pre><label className="checkbox"><input type="checkbox" checked={reviewed} onChange={event=>onReviewed(event.target.checked)}/>I reviewed this root Dockerfile.</label>
      </details>
      {prepared.quality&&<details className="review-section"><summary>Dockerfile quality report ? {prepared.quality.distroless?'Distroless':'Minimal runtime'}</summary><p className="field-help">Runtime: {prepared.quality.runtimeImage}. Static quality checks only; image build, HTTP verification and image CVE scanning have not run.</p><ul className="doctor-findings">{prepared.quality.checks.map((check,index)=><li key={index}><strong>{check.passed?'Passed':check.critical?'Failed':'Review'}: {check.name}</strong><p>{check.details}</p></li>)}</ul></details>}
      {prepared.scout&&<details className="review-section"><summary>Security report</summary><pre>{[prepared.scout.summary,prepared.scout.gate_reason,...prepared.scout.details.map(item=>`${item.id||'Unknown CVE'} [${item.severity||'unknown'}]\nPackage: ${(item.packages||[]).join(', ')}\nLocation: ${(item.paths||[]).join(', ')}\nFixed version: ${item.fixed_version||'not reported'}`)].filter(Boolean).join('\n\n')}</pre></details>}
      {prepared.scout&&!prepared.scout.gate_passed&&!prepared.scout.bypassed&&<><details className="review-section advanced-review"><summary>Advanced options <small>Skip Scout</small></summary><label className="checkbox"><input type="checkbox" checked={bypassAcknowledged} onChange={event=>setBypassAcknowledged(event.target.checked)}/>I explicitly choose to bypass Scout. Security will NOT be verified.</label>{bypassAcknowledged&&<button className="button danger" disabled={busy} onClick={()=>void bypassScout()}>Prepare with Scout bypass</button>}</details></>}
      {reviewed&&<button className="button" disabled={busy} onClick={()=>void downloadZip()}><Download size={15}/>Download prepared ZIP</button>}
      <p className="field-help review-footnote">Source checks only. Image not built or scanned.</p>
    </div>}

    {error&&<p className="form-error" role="alert">{error}</p>}
  </div>;
}
