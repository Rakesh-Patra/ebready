import { afterEach, describe, expect, it } from 'vitest';
import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { serveDashboard } from './static.mjs';

const cleanups = [];
afterEach(async () => { for (const clean of cleanups.splice(0).reverse()) await clean(); });
async function fixture() {
  const root = await mkdtemp(path.join(tmpdir(), 'ebkit-static-'));
  cleanups.push(() => rm(root, { recursive: true, force: true }));
  await mkdir(path.join(root, 'assets'));
  await writeFile(path.join(root, 'index.html'), '<html><head></head><body>Dashboard</body></html>');
  await writeFile(path.join(root, 'assets', 'app.js'), 'console.log("app")');
  await writeFile(path.join(root, '.env'), 'SECRET=private');
  const server = createServer(async (req, res) => {
    if (!await serveDashboard(req, res, root, { anonKey: '</script><script>unsafe', apiKey: 'admin-secret' })) { res.writeHead(401); res.end('API'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  cleanups.push(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  return `http://127.0.0.1:${server.address().port}`;
}
describe('production dashboard hosting', () => {
  it('serves SPA routes and injects only escaped public configuration', async () => {
    const url = await fixture();
    const response = await fetch(url + '/projects/demo');
    expect(response.status).toBe(200);
    const html = await response.text();
    expect(html).toContain('Dashboard');
    expect(html).toContain('window.__EBKIT_PUBLIC_CONFIG__');
    expect(html).toContain('\\u003c/script>');
    expect(html).not.toContain('admin-secret');
    expect(html).not.toContain('<script>unsafe');
  });
  it('serves assets and HEAD without returning HTML for missing assets', async () => {
    const url = await fixture();
    const asset = await fetch(url + '/assets/app.js');
    expect(asset.headers.get('content-type')).toContain('javascript');
    expect(await asset.text()).toContain('console.log');
    const head = await fetch(url, { method: 'HEAD' });
    expect(head.status).toBe(200); expect(await head.text()).toBe('');
    expect((await fetch(url + '/assets/missing.js')).status).toBe(404);
  });
  it('keeps API/auth routes out of the SPA and blocks private paths and methods', async () => {
    const url = await fixture();
    for (const route of ['/api/health', '/insforge/api/auth/sessions']) expect((await fetch(url + route)).status).toBe(401);
    for (const route of ['/.env', '/%2eenv', '/assets/%2e%2e%5c.env']) expect((await fetch(url + route)).status).toBe(404);
    expect((await fetch(url, { method: 'POST' })).status).toBe(405);
  });
});
