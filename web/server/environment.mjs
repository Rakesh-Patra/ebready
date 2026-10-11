const detectionCache = new Map();
// Inspect public templates/source text only; never execute repository code or return values.
const managed = new Set(['PORT','HOST','NODE_ENV','PYTHONUNBUFFERED','HOME','PATH']);
const connection = /^(DATABASE_URL|POSTGRES_URL|POSTGRESQL_URL|MONGODB_URI|MONGO_URI|REDIS_URL|MYSQL_URL|DB_URL)$/;
export function inspectEnvironment(files) {
  const variables = new Map(), runtimeNames = new Set();
  const add = (name, required, source) => {
    if (managed.has(name) || !/^[A-Z][A-Z0-9_]*$/.test(name)) return;
    const old = variables.get(name); variables.set(name, { name, required: required || !!old?.required, source: old?.source || source });
  };
  for (const {path,text} of files) {
    if (/(?:^|\/)\.env\.(?:example|sample|template)$|(?:^|\/)example\.env$/.test(path)) {
      let optional = false;
      for (const line of text.split(/\r?\n/)) {
        if (/^\s*#/.test(line)) { optional = /\boptional\b/i.test(line); continue; }
        const match = line.match(/^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
        if (!match) continue;
        const value = match[2].split(/\s+#/)[0].trim().replace(/^(['"])(.*)\1$/, '$2');
        const required = !optional && (!value || connection.test(match[1]) || /your[_-]|changeme|replace[_-]|<[^>]+>/i.test(value));
        add(match[1], required, path); optional = false;
      }
    } else {
      const patterns = [ /(?:os\.(?:Getenv|LookupEnv)|getenv|System\.getenv|ENV\.fetch)\(\s*['"]([A-Z][A-Z0-9_]*)['"]/g, /(?:process\.env|os\.environ|ENV)\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]/g, /process\.env\.([A-Z][A-Z0-9_]*)/g, /(?:os\.environ\.get|os\.getenv)\(\s*['"]([A-Z][A-Z0-9_]*)['"]/g ];
      for (const pattern of patterns) for (const match of text.matchAll(pattern)) { runtimeNames.add(match[1]); add(match[1], connection.test(match[1]), path); }
    }
  }
  return [...variables.values()].filter(item => !runtimeNames.size || runtimeNames.has(item.name)).sort((a,b)=>a.name.localeCompare(b.name));
}
export async function discoverEnvironment(api, branch, get) {
  const tree = await get(`${api}/git/trees/${encodeURIComponent(branch)}?recursive=1`, 'Repository file list could not be checked.');
  const cacheKey = tree.sha ? `${api}:${tree.sha}` : null;
  const cached = cacheKey && detectionCache.get(cacheKey);
  if (cached && cached.expires > Date.now()) return cached.result;
  const paths = (tree.tree || []).filter(x => x.type === 'blob' && x.size <= 65536 && !/(?:^|\/)(?:node_modules|vendor|\.git|dist|build|tests?)(?:\/|$)|(?:_test\.|\.test\.|\.spec\.)/.test(x.path));
  const templates = paths.filter(x=>/(?:^|\/)\.env\.(?:example|sample|template)$|(?:^|\/)example\.env$/.test(x.path));
  const sources = paths.filter(x=>/\.(?:go|py|js|ts|tsx|jsx|java|rb|php|cs)$/.test(x.path));
  const selected = [...templates, ...sources].slice(0,40);
  const files = [];
  for (let i=0;i<selected.length;i+=4) {
    files.push(...await Promise.all(selected.slice(i,i+4).map(async file=> {
      const result = await get(`${api}/contents/${file.path.split('/').map(encodeURIComponent).join('/')}?ref=${encodeURIComponent(branch)}`, 'Repository changed during validation; try again.');
      return {path:file.path,text:result.encoding === 'base64' && result.size <= 65536 ? Buffer.from(result.content || '', 'base64').toString('utf8') : ''};
    })));
  }
  const result = { environment:inspectEnvironment(files), environmentScanComplete: !!tree.tree && !tree.truncated && templates.length+sources.length <=40 };
  if (cacheKey) { if (detectionCache.size >= 64) detectionCache.delete(detectionCache.keys().next().value); detectionCache.set(cacheKey, {result, expires:Date.now()+600000}); }
  return result;
}
export function requireEnvironment(variables, values) {
  const missing = variables.filter(x=>x.required && (typeof values?.[x.name] !== 'string' || !values[x.name].trim() || /^(?:your[_ -]|changeme|replace[_ -]|<)/i.test(values[x.name]))).map(x=>x.name);
  return missing;
}
