import { readFileSync } from 'node:fs';
import { createWorker } from './worker.mjs';
for (const line of readFileSync(new URL('./.env', import.meta.url), 'utf8').split(/\r?\n/)) {
  if (!line || line.startsWith('#')) continue;
  const index = line.indexOf('='); process.env[line.slice(0,index)] ??= line.slice(index+1);
}
const config = {
  baseUrl: process.env.INSFORGE_URL, anonKey: process.env.INSFORGE_ANON_KEY,
  apiKey: process.env.INSFORGE_API_KEY, python: process.env.EBKIT_PYTHON || 'python',
  liveEnabled: process.env.EBKIT_LIVE_ENABLED === 'true',
  allowedEmails: (process.env.EBKIT_ALLOWED_EMAILS || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean),
  origins: (process.env.EBKIT_WEB_ORIGINS || 'http://127.0.0.1:5173,http://localhost:5173').split(','),
};
const server = await createWorker(config);
server.on('error', error => { console.error(error.code === 'EADDRINUSE' ? 'Port 8787 is already in use. Stop the existing EBKit backend before starting another.' : 'Backend could not listen on its configured port.'); process.exitCode = 1; });
server.listen(8787, '127.0.0.1', () => console.log('EBKit backend listening on 127.0.0.1:8787. No AWS calls are made until an authorized operation is submitted.'));
