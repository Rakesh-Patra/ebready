import { readFileSync } from 'node:fs';
import { createWorker } from './worker.mjs';
import path from 'node:path';
import { createServer as createPortProbe } from 'node:net';
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
  ...(process.env.EBKIT_JOBS_DIR ? { jobRoot: path.resolve(process.env.EBKIT_JOBS_DIR) } : {}),
  geminiKey: process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY,
  geminiModel: process.env.EBKIT_GEMINI_MODEL || 'gemini-2.5-flash',
};
// Check the listener before recovery changes database flags (including during an upgrade).
try {
  await new Promise((resolve, reject) => {
    const probe = createPortProbe();
    probe.once('error', reject);
    probe.listen(8787, '127.0.0.1', () => probe.close(resolve));
  });
} catch {
  console.error('Port 8787 is unavailable. Stop the existing EBKit backend before starting another.');
  process.exit(1);
}
const server = await createWorker(config);
server.on('error', error => { console.error(error.code === 'EADDRINUSE' ? 'Port 8787 is already in use. Stop the existing EBKit backend before starting another.' : 'Backend could not listen on its configured port.'); process.exitCode = 1; });
server.listen(8787, '127.0.0.1', () => console.log('EBKit backend listening on 127.0.0.1:8787. Existing authorized jobs are recovered; startup creates no AWS operations.'));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.close(() => process.exit(0)));
