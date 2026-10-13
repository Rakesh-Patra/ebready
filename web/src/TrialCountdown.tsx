import {useEffect,useState} from 'react';
import type {Project} from './lib';
export function trialExpired(project:Project){return (project.plan==='free_trial'||project.plan==='paid_test')&&(!project.trial_expires_at||Date.parse(project.trial_expires_at)<=Date.now()||project.trial_cleanup_state!=='scheduled');}
export default function TrialCountdown({project}:{project:Project}){
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[]);
  if((project.plan!=='free_trial'&&project.plan!=='paid_test')||!project.trial_expires_at)return null;
  const label=project.plan==='paid_test'?'Test pass':'Trial';
  if(project.trial_cleanup_state==='complete')return <p className="trial-countdown">{label} ended · cleanup verified</p>;
  if(project.trial_cleanup_state!=='scheduled')return <p className="trial-countdown expired">{label} ended · cleanup {project.trial_cleanup_state==='verifying'?'verification pending':'in progress'}</p>;
  const remaining=Math.max(0,Math.ceil((Date.parse(project.trial_expires_at)-now)/1000));
  return <p className={`trial-countdown ${remaining===0?'expired':''}`} role="timer">{remaining===0?`${label} expired · cleanup queued`:`${project.trial_started_at?(project.plan==='paid_test'?'Two-hour test pass':'Free trial'):'Launch deadline'} · ${Math.floor(remaining/60).toString().padStart(2,'0')}:${(remaining%60).toString().padStart(2,'0')} remaining`}</p>;
}
