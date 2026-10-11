import { createClient } from '@insforge/sdk';
export const insforge = createClient({
  baseUrl: import.meta.env.VITE_INSFORGE_URL,
  anonKey: import.meta.env.VITE_INSFORGE_ANON_KEY,
});
export type Project = { id: string; user_id: string; name: string; repository: string; region: string; port: number; status: string; stage: number; replicas: number; revision: number; variable_names: string[]; created_at: string; mode?: 'demo' | 'live'; application_name?: string; environment_name?: string; live_url?: string; health?: string; last_error?: string; operation_log?: string; last_operation?: string; operation_running?: boolean };
export const columns = 'id,user_id,name,repository,region,port,status,stage,replicas,revision,variable_names,created_at,mode,application_name,environment_name,live_url,health,last_error,operation_log,last_operation,operation_running';
export async function backend<T>(route: string, body: unknown): Promise<T> {
  const token = route === '/api/repository' ? null : await insforge.getHttpClient().getValidAccessToken();
  let response: Response;
  try { response = await fetch(route, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(body) }); }
  catch { throw new Error('The backend is unavailable. Start the EBKit backend worker and try again.'); }
  let data; try { data = await response.json(); } catch { throw new Error('The backend is unavailable. Start the EBKit backend worker.'); }
  if (!response.ok) throw new Error(data.error || 'Backend request failed.');
  return data as T;
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
export function message(error: unknown) { return error instanceof Error ? error.message : 'The request could not be completed. Please try again.'; }
