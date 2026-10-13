import { useState } from 'react';
import { backend, message, parseRepository } from './lib';
import ZipSource, {type PreparedZip} from './ZipSource';

type Snapshot={uploadId:string;port:number;repository:string;commit:string;name:string};
export default function GithubDoctor({repository,signedIn,login,onUse}:{repository:string;signedIn:boolean;login:()=>void;onUse:(draft:PreparedZip)=>void}) {
  const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[prepared,setPrepared]=useState<PreparedZip|null>(null),[reviewed,setReviewed]=useState(false),[port,setPort]=useState(8080),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function importSource(){
    if(!signedIn){login();return;}
    setBusy(true);setError('');
    try {
      const parsed=parseRepository(repository);
      const result=await backend<Snapshot>('/api/repository/import',{repository:parsed.url});
      setSnapshot(result);setPort(result.port);setPrepared(null);setReviewed(false);
    }catch(problem){setError(message(problem));}finally{setBusy(false);}
  }
  return <section aria-label="GitHub deployment doctor" className="github-doctor">
    <button type="button" className="button" disabled={busy||!repository.trim()} onClick={()=>void importSource()}>{busy?'Importing GitHub snapshot...':signedIn?'Prepare Dockerfile from GitHub snapshot':'Log in to prepare from GitHub'}</button>
    <p className="field-help">Import a commit-pinned copy of the public default branch. If its root Dockerfile is missing, approve AI preparation below to generate and review one, including multi-tier projects. Your GitHub repository is unchanged; deploy the reviewed snapshot as a ZIP or download and commit the Dockerfile yourself.</p>
    {snapshot&&<><p className="field-help">{snapshot.repository} · commit <code>{snapshot.commit.slice(0,12)}</code></p>
      <ZipSource key={snapshot.uploadId} initialUpload={snapshot} prepared={prepared} onPrepared={setPrepared} reviewed={reviewed} onReviewed={setReviewed} signedIn={signedIn} login={login} port={port} onPort={setPort}/>
      {prepared&&reviewed&&(prepared.scout?.gate_passed||prepared.scout?.bypassed)&&<button type="button" className="button primary" onClick={()=>onUse(prepared)}>Use reviewed GitHub snapshot for deployment</button>}
    </>}
    {error&&<p className="form-error" role="alert">{error}</p>}
  </section>;
}
