// Detached from HTTP: a backend restart must not stop or duplicate an AWS command.
import { spawn } from 'node:child_process';
import { readFile, rm } from 'node:fs/promises';
import { atomicJSON, readJSON, safeOutput } from './jobs.mjs';

const file = process.argv[2];
const job = await readJSON(file);
const input = await readJSON(job.inputFile);
const redact = (text, secrets) => {
  let result = text.replace(/\x1b\[[0-9;]*m/g, '');
  for (const value of secrets.filter(Boolean).sort((a,b) => b.length-a.length)) result = result.split(value).join('[REDACTED]');
  return result.replace(/(?:AKIA|ASIA)[A-Z0-9]{16}/g, '[REDACTED AWS KEY]');
};
const secrets = input.secretFile ? (await readFile(input.secretFile, 'utf8')).split('\n').map(line => line.slice(line.indexOf('=') + 2, -1)).filter(Boolean) : [];
job.runnerPid = process.pid;
job.state = 'running';
job.heartbeat = Date.now();
await atomicJSON(file, job);
let raw = '', child, finished = false, writes = Promise.resolve();
const persist = () => {
  job.output = safeOutput(raw, secrets, redact);
  job.heartbeat = Date.now();
  const snapshot = structuredClone(job);
  writes = writes.catch(() => {}).then(() => atomicJSON(file, snapshot));
  return writes;
};
const heartbeat = setInterval(() => { void persist().catch(() => { job.problem = 'Job progress could not be saved. Check AWS status before retrying.'; child?.kill(); }); }, 1000);
const timeout = setTimeout(() => { job.problem = 'Operation timed out. Check AWS status before retrying.'; child?.kill(); }, Math.max(1, job.deadline - Date.now()));
const finish = async code => {
  if (finished) return;
  finished = true;
  clearInterval(heartbeat); clearTimeout(timeout);
  job.state = 'finished'; job.code = job.problem ? 1 : code ?? 1;
  if (input.secretFile) await rm(input.secretFile, { force: true }).catch(() => {});
  await persist();
};
try {
  const env = { ...process.env, ...input.env };
  delete env.INSFORGE_API_KEY;
  delete env.GEMINI_API_KEY; delete env.GOOGLE_API_KEY;
  child = spawn(input.python, input.args, { cwd: input.cwd, env, shell: false, windowsHide: true, stdio: ['pipe','pipe','pipe'] });
  job.childPid = child.pid;
  const append = chunk => {
    raw += chunk.toString();
    // Keep overlap longer than the longest allowed secret; trim only after redaction.
    if (raw.length > 64000) raw = raw.slice(-56000);
  };
  child.stdout.on('data', append); child.stderr.on('data', append);
  child.once('error', () => { job.problem = 'Could not start EBKit. Check EBKIT_PYTHON and the CLI installation.'; void finish(1).catch(() => { process.exitCode = 1; }); });
  child.once('close', code => void finish(code).catch(() => { process.exitCode = 1; }));
  child.stdin.on('error', () => {});
  child.stdin.end(input.stdin);
  await persist();
} catch {
  job.problem = 'The job runner could not launch EBKit. Check status before retrying.';
  await finish(1);
}
