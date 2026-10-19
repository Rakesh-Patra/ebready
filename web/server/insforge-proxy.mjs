import http from 'node:http';
import https from 'node:https';

// A fixed upstream keeps the SDK's refresh cookie on the application's origin.
// Never add the server admin key: requests retain the browser's own credentials.
export function proxyInsforge(req, res, baseUrl) {
  const incoming = new URL(req.url, 'http://localhost');
  const pathname = incoming.pathname.slice('/insforge'.length);
  if (!/^\/api\/(auth|database)(\/|$)/.test(pathname)) {
    res.writeHead(404).end();
    return Promise.resolve();
  }
  const target = new URL(baseUrl);
  target.pathname = pathname;
  target.search = incoming.search;
  const headers = { ...req.headers, host: target.host };
  for (const key of ['connection', 'transfer-encoding', 'upgrade', 'proxy-authorization', 'proxy-connection']) delete headers[key];
  return new Promise(resolve => {
    const fail = () => {
      if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      if (!res.writableEnded) res.end(JSON.stringify({ message: 'InsForge is temporarily unreachable. Please try again.', code: 'INSFORGE_UNAVAILABLE' }));
      resolve();
    };
    const upstream = (target.protocol === 'https:' ? https : http).request(target, { method: req.method, headers }, response => {
      const outgoing = { ...response.headers, 'cache-control': 'no-store' };
      delete outgoing['transfer-encoding'];
      delete outgoing.connection;
      // Preserve HttpOnly, Secure, SameSite and expiry; remove only the remote domain.
      if (outgoing['set-cookie']) outgoing['set-cookie'] = outgoing['set-cookie'].map(cookie => cookie.replace(/;\s*Domain=[^;]*/ig, '').replace(/;\s*Path=[^;]*/ig, '; Path=/'));
      res.writeHead(response.statusCode, outgoing);
      response.on('error', () => { res.destroy(); resolve(); });
      response.pipe(res);
      response.on('end', resolve);
    });
    upstream.setTimeout(45000, () => upstream.destroy(new Error('Upstream timeout')));
    upstream.on('error', fail);
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => { upstream.destroy(); resolve(); });
    req.pipe(upstream);
  });
}
