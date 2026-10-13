import { mkdir, readFile, writeFile, rename, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

export async function atomicJSON(file, value, replace = rename) {
  const temporary = `${file}.${randomUUID()}.tmp`;
  await writeFile(temporary, JSON.stringify(value), { mode: 0o600 });
  try {
    for (let attempt = 0; ; attempt++) {
      try { await replace(temporary, file); break; }
      catch (error) {
        if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= 7) throw error;
        await new Promise(resolve => setTimeout(resolve, Math.min(500, 25 * 2 ** attempt)));
      }
    }
  } finally { await rm(temporary, { force: true }); }
}
export async function readJSON(file) { return JSON.parse(await readFile(file, 'utf8')); }
export async function removeJobDirectory(root, directory) {
  const base = path.resolve(root), target = path.resolve(directory);
  if (!target.startsWith(base + path.sep) || !path.basename(target).startsWith('operation-')) throw new Error('Invalid job cleanup path.');
  await rm(target, { recursive: true, force: true });
}
export function alive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code === 'EPERM'; }
}

// One worker per persistent directory: never let two backends replay the same job.
export async function lockJobs(directory) {
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const file = path.join(directory, 'worker.lock');
  const token = randomUUID();
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await writeFile(file, JSON.stringify({ pid: process.pid, token }), { flag: 'wx', mode: 0o600 });
      return async () => { if ((await readJSON(file).catch(() => null))?.token === token) await rm(file, { force: true }); };
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
      const owner = await readJSON(file).catch(() => null);
      if (!owner || alive(owner.pid)) throw new Error('Another worker owns the job directory, or its lock is unreadable.');
      await rm(file, { force: true });
    }
  }
  throw new Error('Could not acquire the worker job lock.');
}

export async function pendingJobs(directory) {
  const files = (await readdir(directory)).filter(name => /^journal-[0-9a-f-]{36}\.json$/.test(name));
  const result = [];
  for (const name of files) {
    const job = await readJSON(path.join(directory, name));
    if (!job.applied) result.push({ file: path.join(directory, name), job });
  }
  return result;
}

// Never persist a suffix that might be the first part of a split secret.
export function safeOutput(raw, secrets, redact) {
  let output = raw;
  for (const secret of secrets.filter(Boolean)) {
    for (let size = Math.min(secret.length - 1, output.length); size > 0; size--) {
      if (output.endsWith(secret.slice(0, size))) { output = output.slice(0, -size) + '[REDACTED]'; break; }
    }
  }
  return redact(output, secrets).slice(-48000);
}
