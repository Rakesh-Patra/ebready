import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';

const fail = message => Object.assign(new Error(message), { status: 422 });
async function files(root, prefix = '') {
  const result = [];
  for (const entry of await readdir(path.join(root, prefix), { withFileTypes: true })) {
    const name = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory() && !['node_modules', '.git'].includes(entry.name)) result.push(...await files(root, name));
    else if (entry.isFile()) result.push(name);
  }
  return result;
}
const major = value => /^\s*[~^]?(\d+)/.exec(value ?? '')?.[1];
export async function checkBuildInputs(root, dockerfile = null) {
  const names = await files(root);
  for (const name of names.filter(name => name.endsWith('package.json'))) {
    let manifest;
    try { manifest = JSON.parse(await readFile(path.join(root, name), 'utf8')); } catch { throw fail(`Invalid ${name}. Fix the manifest and upload a new ZIP.`); }
    const deps = { ...manifest.dependencies, ...manifest.devDependencies };
    const vite = Number(major(deps.vite)), react = Number(major(deps['@vitejs/plugin-react']));
    if (vite >= 8 && react > 0 && react < 5) throw fail(`${name}: Vite ${vite} conflicts with React plugin ${react}. Update to a compatible plugin and regenerate the lockfile; upload a new ZIP.`);
    if (dockerfile && vite >= 7 && /^\s*FROM\s+(?:docker\.io\/library\/)?node:(?:1[0-9]|20\.(?:[0-9]|1[0-8]))(?:[.\s-]|$)/im.test(dockerfile)) throw fail(`Dockerfile uses an incompatible Node version for ${name}. Use Node 22.12+ or a supported newer version.`);
  }
  if (!dockerfile) return;
  for (const line of dockerfile.replace(/\\\r?\n/g, ' ').split(/\r?\n/)) {
    const match = /^\s*(?:COPY|ADD)\s+(.+)$/i.exec(line);
    if (!match || /--from(?:=|\s)/i.test(match[1])) continue;
    const text = match[1].replace(/--\S+(?:=\S+)?\s*/g, '').trim();
    let tokens;
    try { tokens = text.startsWith('[') ? JSON.parse(text) : text.match(/"[^"]*"|'[^']*'|\S+/g).map(token => token.replace(/^["']|["']$/g, '')); } catch { continue; }
    const sources = tokens.slice(0, -1);
    // Docker permits wildcard groups when at least one source matches.
    if (sources.some(source => /[$]|^https?:\/\//.test(source))) continue;
    const exists = source => {
      source = source.replace(/^\.\//, '').replace(/^\//, '').replace(/\/$/, '');
      if (source === '.' || source === '') return true;
      const pattern = new RegExp(`^${source.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}(?:/|$)`);
      return names.some(name => pattern.test(name));
    };
    const missing = sources.filter(source => !/[*?]/.test(source) && !exists(source));
    if (missing.length || (sources.length && !sources.some(exists))) throw fail(`Dockerfile copies missing source: ${(missing.length ? missing : sources).join(', ')}. Build generated assets in a Docker stage and use COPY --from, or include the required source files.`);
  }
}
