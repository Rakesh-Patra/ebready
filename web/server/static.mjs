import { readFile, realpath } from 'node:fs/promises';
import path from 'node:path';

const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.json': 'application/json' };

// Serve only the Vite output directory; API and authentication stay with the worker.
export async function serveDashboard(req, res, root, publicConfig = {}) {
  const route = new URL(req.url, 'http://localhost').pathname;
  if (route === '/api' || route.startsWith('/api/') || route === '/insforge' || route.startsWith('/insforge/')) return false;
  const finish = (status, body = '') => { res.writeHead(status, { 'Content-Type': 'text/plain', 'Cache-Control': 'no-store' }); res.end(body); return true; };
  if (!['GET', 'HEAD'].includes(req.method)) return finish(405);
  let decoded;
  try { decoded = decodeURIComponent(route); } catch { return finish(400); }
  if (decoded.includes('\\') || decoded.includes('\0') || decoded.split('/').some(part => part.startsWith('.'))) return finish(404);
  const base = await realpath(root);
  let candidate = path.resolve(base, `.${decoded}`);
  if (candidate !== base && !candidate.startsWith(base + path.sep)) return finish(404);
  if (decoded === '/') candidate = path.join(base, 'index.html');
  try {
    // Unknown client routes use the SPA; missing assets remain 404s.
    try { candidate = await realpath(candidate); }
    catch (error) {
      if (error.code !== 'ENOENT' || path.extname(decoded) || decoded.startsWith('/assets/')) return finish(404);
      candidate = await realpath(path.join(base, 'index.html'));
    }
    if (!candidate.startsWith(base + path.sep)) return finish(404);
    let body = await readFile(candidate);
    if (candidate === path.join(base, 'index.html')) {
      const settings = JSON.stringify({ baseUrl: publicConfig.baseUrl || '', anonKey: publicConfig.anonKey || '' }).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
      body = Buffer.from(body.toString('utf8').replace('</head>', `<script>window.__EBKIT_PUBLIC_CONFIG__=${settings};</script></head>`));
    }
    res.writeHead(200, { 'Content-Type': types[path.extname(candidate)] || 'application/octet-stream', 'Content-Length': body.length, 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' });
    res.end(req.method === 'HEAD' ? undefined : body);
    return true;
  } catch { return finish(404); }
}
