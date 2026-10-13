import { createClient } from '@insforge/sdk';
export const insforge = createClient({
  baseUrl: import.meta.env.VITE_INSFORGE_URL,
  anonKey: import.meta.env.VITE_INSFORGE_ANON_KEY,
});
export type Project = { id: string; user_id: string; name: string; repository: string; region: string; port: number; status: string; stage: number; replicas: number; revision: number; variable_names: string[]; created_at: string; plan?:'free_trial'|'paid_test';trial_started_at?:string;trial_expires_at?:string;trial_cleanup_state?:string; mode?: 'demo' | 'live'; application_name?: string; environment_name?: string; live_url?: string; health?: string; last_error?: string; operation_log?: string; last_operation?: string; operation_running?: boolean };
export const columns = 'id,user_id,name,repository,region,port,status,stage,replicas,revision,variable_names,created_at,mode,application_name,environment_name,live_url,health,last_error,operation_log,last_operation,operation_running,plan,trial_started_at,trial_expires_at,trial_cleanup_state';
async function authenticatedToken(){
  const {data,error}=await insforge.auth.getCurrentUser();
  if(error||!data?.user)throw new Error('Log in to use cloud controls.');
  const token=await insforge.getHttpClient().getValidAccessToken();
  if(!token)throw new Error('Your session has ended. Log in again.');
  return token;
}
export async function backend<T>(route: string, body: unknown): Promise<T> {
  const token = route === '/api/repository' ? null : await authenticatedToken();
  let response: Response;
  try { response = await fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(body) }); }
  catch { throw new Error('The backend is unavailable. Start the EBKit backend worker and try again.'); }
  let data; try { data = await response.json(); } catch { throw new Error('The backend is unavailable. Start the EBKit backend worker.'); }
  if (!response.ok) throw new Error(data.error || 'Backend request failed.');
  return data as T;
}
export async function uploadZip(file: File): Promise<{uploadId:string;port:number}> {
  const token=await authenticatedToken();
  const response=await fetch('/api/uploads',{method:'POST',headers:{'Content-Type':'application/zip',Authorization:`Bearer ${token}`,'x-ebkit-filename':encodeURIComponent(file.name)},body:file});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'ZIP upload failed.');return data;
}
export async function downloadPreparedZip(uploadId:string,reviewHash:string):Promise<Blob> {
  const token=await authenticatedToken();
  const response=await fetch(`/api/uploads/${uploadId}/download`,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},body:JSON.stringify({reviewed:true,reviewHash})});
  if(!response.ok){const data=await response.json();throw new Error(data.error||'ZIP download failed.');}return response.blob();
}
export function parseRepository(input: string) {
  const match = input.trim().match(/^https:\/\/github\.com\/([\w-]+)\/([\w.-]+?)(?:\.git)?\/?$/);
  if (!match) throw new Error('Enter a public GitHub repository URL, such as https://github.com/owner/project.');
  return { owner: match[1], repo: match[2], url: `https://github.com/${match[1]}/${match[2]}` };
}
export function parseEnv(text: string) {
  const values: Record<string, string> = Object.create(null);
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim(); if (!line || line.startsWith('#')) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) throw new Error('Each setting must use NAME=VALUE.');
    let value = match[2];
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    values[match[1]] = value;
  }
  return values;
}
export function quoteEnvValues(text:string){
  return text.split(/\r?\n/).map(line=>{
    const match=line.match(/^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(.*)$/);
    if(!match)return line;
    const value=match[2].trim();
    if(!value||value.includes('"')||(value.startsWith("'")&&value.endsWith("'")))return line;
    if(/^[a-z][a-z0-9+.-]*:\/\//i.test(value)||/[\s&#?;=$]/.test(value))return `${match[1]}"${value}"`;
    return line;
  }).join('\n');
}
export function message(error: unknown) { return error instanceof Error ? error.message : 'The request could not be completed. Please try again.'; }

export type TrialAccount={approved:boolean;trialUsed:boolean;durationMinutes:number|null;capacity?:number;testing?:boolean;paidTestAvailable?:boolean;paidTestDurationMinutes?:number};
export const getTrialAccount=()=>backend<TrialAccount>('/api/trial',{});
