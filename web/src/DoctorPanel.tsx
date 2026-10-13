import { useState } from 'react';
import { Stethoscope } from 'lucide-react';
import { backend, message } from './lib';

export type DoctorReport = {
  port: number; planHash: string; scanComplete: boolean; applied?: string[];
  findings: Array<{code:string;path:string;line:number;message:string;fixable:boolean}>;
  patches: Array<{path:string;diff:string}>;
  verification: {static:string;build:string;runtime:string;security:string};
};

export default function DoctorPanel({uploadId,port,disabled,onApplied}:{uploadId:string;port:number;disabled:boolean;onApplied:()=>void}) {
  const [report,setReport]=useState<DoctorReport|null>(null),[busy,setBusy]=useState(false),[approved,setApproved]=useState(false),[error,setError]=useState('');
  async function run(apply=false) {
    setBusy(true);setError('');
    try {
      const result=await backend<DoctorReport>(`/api/uploads/${uploadId}/doctor`,apply?{port,apply:true,reviewed:true,planHash:report?.planHash}:{port});
      setReport(result);setApproved(false);
      if(apply)onApplied();
    } catch(problem){setError(message(problem));setApproved(false);}
    finally {setBusy(false);}
  }
  return <section className="doctor-panel" aria-label="Deployment doctor">
    <div className="section-heading"><strong><Stethoscope size={18}/> Deployment doctor</strong><button type="button" className="button small" disabled={disabled||busy||!Number.isInteger(port)||port<1||port>65535} onClick={()=>void run()}>{busy?'Checking project...':report?'Check again':'Check deployment blockers'}</button></div>
    <p className="field-help">Find startup, port, runtime-version and missing build-input problems before deployment. Review suggested repairs before changing your uploaded copy.</p>
    {report&&<>
      <p role="status">{report.applied?.length?`Repaired ${report.applied.length} file(s). Prepare and review the Dockerfile again.`:report.findings.length?`${report.findings.length} finding(s) · ${report.patches.length} proposed file repair(s)`:'No blockers found by these static checks.'}</p>
      {!!report.findings.length&&<ul className="doctor-findings">{report.findings.map((item,index)=><li key={`${item.code}-${index}`}><strong>{item.path}:{item.line}</strong><p>{item.message}</p><span>{item.fixable?'Repair proposed':'Manual review needed'}</span></li>)}</ul>}
      {report.patches.map(patch=><details className="review-section" key={patch.path}><summary>Proposed repair: {patch.path}</summary><pre>{patch.diff}</pre></details>)}
      {!!report.patches.length&&<><label className="checkbox"><input type="checkbox" disabled={busy||disabled} checked={approved} onChange={event=>setApproved(event.target.checked)}/>I reviewed the proposed diffs and approve these repairs.</label><button type="button" className="button primary" disabled={!approved||disabled||busy} onClick={()=>void run(true)}>Apply reviewed repairs</button></>}
      <div className="doctor-verification"><span>Static checks: {report.verification.static}</span><span>Build: not verified</span><span>HTTP runtime: not verified</span><span>Security: not verified by doctor</span></div>
      <p className="field-help">These checks are heuristic. The website does not execute uploaded code. Download the reviewed prepared ZIP, extract it, then build and probe it locally:</p>
      <pre>ebkit doctor --path ./your-project --port {port} --verify</pre>
    </>}
    {error&&<p className="form-error" role="alert">{error}</p>}
  </section>;
}
