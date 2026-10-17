import { afterEach, expect, it } from 'vitest';
import { createServer } from 'node:http';
import { proxyInsforge } from './insforge-proxy.mjs';

const servers = [];
afterEach(async () => { for (const server of servers.splice(0)) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); } });
async function listen(handler) {
  const server = createServer(handler); servers.push(server);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

it('relays login credentials and refresh cookies without adding admin credentials', async () => {
  const seen = [];
  const target = await listen(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    seen.push({ url: req.url, headers: req.headers, body });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': [
      'insforge_refresh_token=test; Domain=remote.example; Path=/api/auth; HttpOnly; Secure; SameSite=None',
      'insforge_csrf_token=csrf; Path=/; SameSite=Lax',
    ] }); res.end('{"ok":true}');
  });
  const app = await listen((req, res) => proxyInsforge(req, res, target));
  const response = await fetch(app + '/insforge/api/auth/refresh?test=1', {
    method: 'POST', headers: { Authorization: 'Bearer user-token', Cookie: 'insforge_refresh_token=test', 'X-CSRF-Token': 'csrf', 'Content-Type': 'application/json' }, body: '{}',
  });
  expect(await response.json()).toEqual({ ok: true });
  expect(seen[0].url).toBe('/api/auth/refresh?test=1');
  expect(seen[0].headers.authorization).toBe('Bearer user-token');
  expect(seen[0].headers.cookie).toBe('insforge_refresh_token=test');
  expect(seen[0].headers['x-csrf-token']).toBe('csrf');
  expect(seen[0].body).toBe('{}');
  expect(response.headers.getSetCookie()).toEqual([
    'insforge_refresh_token=test; Path=/; HttpOnly; Secure; SameSite=None',
    'insforge_csrf_token=csrf; Path=/; SameSite=Lax',
  ]);
  expect(response.headers.get('cache-control')).toBe('no-store');
});

it('does not follow redirects with browser credentials or retry writes', async () => {
  let calls = 0;
  const target = await listen((_req, res) => { calls++; res.writeHead(307, { Location: 'https://other.example/' }); res.end(); });
  const app = await listen((req, res) => proxyInsforge(req, res, target));
  const response = await fetch(app + '/insforge/api/database/records/projects', { method: 'POST', body: '{}', redirect: 'manual' });
  expect(response.status).toBe(307); expect(calls).toBe(1);
});

it('rejects non-SDK routes before contacting the upstream', async () => {
  const app = await listen((req, res) => proxyInsforge(req, res, 'http://127.0.0.1:1'));
  expect((await fetch(app + '/insforge/api/secrets')).status).toBe(404);
  const response = await fetch(app + '/insforge/api/auth/public-config');
  expect(response.status).toBe(502); expect((await response.json()).code).toBe('INSFORGE_UNAVAILABLE');
});
