import { readFileSync, existsSync } from 'node:fs';
import { createWorker } from './worker.mjs';
import path from 'node:path';
import { createServer as createPortProbe } from 'node:net';
const localEnv = new URL('./.env', import.meta.url);
for (const line of (existsSync(localEnv) ? readFileSync(localEnv, 'utf8') : '').split(/\r?\n/)) {
  if (!line || line.startsWith('#')) continue;
  const index = line.indexOf('='); if (index > 0) process.env[line.slice(0,index)] ??= line.slice(index+1);
}
const databaseTemplates=process.env.EBKIT_DATABASE_TEMPLATES_FILE?JSON.parse(readFileSync(process.env.EBKIT_DATABASE_TEMPLATES_FILE,'utf8')):{};
const config = {
  publicDemo: process.env.EBKIT_PUBLIC_DEMO === 'true',
  ...(process.env.EBKIT_STATIC_ROOT ? { staticRoot: path.resolve(process.env.EBKIT_STATIC_ROOT) } : {}),
  databaseTemplates,
  baseUrl: process.env.INSFORGE_URL, anonKey: process.env.INSFORGE_ANON_KEY,
  apiKey: process.env.INSFORGE_API_KEY, python: process.env.EBKIT_PYTHON || 'python',
  liveEnabled: process.env.EBKIT_LIVE_ENABLED === 'true',
  awsProfiles:(process.env.EBKIT_AWS_PROFILES||'').split(',').map(value=>value.trim()).filter(Boolean),
  awsAccountId:process.env.EBKIT_AWS_ACCOUNT_ID,
  githubToken: process.env.EBKIT_GITHUB_TOKEN,
  githubAutoDeploy: process.env.EBKIT_GITHUB_AUTO_DEPLOY !== 'false',
  githubPollIntervalMs: Number(process.env.EBKIT_GITHUB_POLL_SECONDS || 300)*1000,
  allowedEmails: (process.env.EBKIT_ALLOWED_EMAILS || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean),
  testingEmails: (process.env.EBKIT_TESTING_EMAILS || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean),
  evaluatorEmails: (process.env.EBKIT_EVALUATOR_EMAILS || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean),
  paidTestEmails: (process.env.EBKIT_PAID_TEST_EMAILS || '').toLowerCase().split(',').map(value => value.trim()).filter(Boolean),
  origins: (process.env.EBKIT_WEB_ORIGINS || 'http://127.0.0.1:5173,http://localhost:5173').split(','),
  ...(process.env.EBKIT_JOBS_DIR ? { jobRoot: path.resolve(process.env.EBKIT_JOBS_DIR) } : {}),
  geminiKey: process.env.GEMINI_API_KEY || process.env.GOOGLE_API_KEY,
  globalCapacity:Number(process.env.EBKIT_GLOBAL_CAPACITY||2),
  geminiModel: process.env.EBKIT_GEMINI_MODEL || 'gemini-2.5-flash',
};
if(!Number.isInteger(config.globalCapacity)||config.globalCapacity<1||config.globalCapacity>10)throw new Error('EBKIT_GLOBAL_CAPACITY must be between 1 and 10.');
if(!Number.isInteger(config.githubPollIntervalMs)||config.githubPollIntervalMs<30000)throw new Error('EBKIT_GITHUB_POLL_SECONDS must be at least 30.');
const port = Number(process.env.PORT || 8787);
const host = process.env.HOST || '127.0.0.1';
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535.');
// Check the listener before recovery changes database flags (including during an upgrade).
try {
  await new Promise((resolve, reject) => {
    const probe = createPortProbe();
    probe.once('error', reject);
    probe.listen(port, host, () => probe.close(resolve));
  });
} catch {
  console.error(`Port ${port} is unavailable. Stop the existing EBKit backend before starting another.`);
  process.exit(1);
}
const server = await createWorker(config);
server.on('error', error => { console.error(error.code === 'EADDRINUSE' ? `Port ${port} is already in use. Stop the existing EBKit backend before starting another.` : 'Backend could not listen on its configured port.'); process.exitCode = 1; });
server.listen(port, host, () => console.log(`EBKit backend listening on ${host}:${port}. Existing authorized jobs are recovered; startup creates no AWS operations.`));
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => server.close(() => process.exit(0)));
