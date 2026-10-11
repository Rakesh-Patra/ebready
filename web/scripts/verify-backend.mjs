import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { createClient } from '@insforge/sdk';

const env = Object.fromEntries(readFileSync(new URL('../.env', import.meta.url), 'utf8').trim().split(/\r?\n/).map(line => {
  const offset = line.indexOf('='); return [line.slice(0, offset), line.slice(offset + 1)];
}));
const client = createClient({ baseUrl: env.VITE_INSFORGE_URL, anonKey: env.VITE_INSFORGE_ANON_KEY });
const { data, error } = await client.auth.signInWithOAuth('google', { redirectTo: 'http://127.0.0.1:5173/', skipBrowserRedirect: true });
assert.equal(error, null, 'Google authorization must start successfully');
assert.equal(new URL(data.url).hostname, 'accounts.google.com', 'Expected Google OpenID Connect provider');
console.log('PASS: Google authorization endpoint and allowed local redirect');
const read = await client.database.from('ebkit_web_projects').select('id').limit(1);
assert.ok(read.error, 'Anonymous users must not read saved projects');
const write = await client.database.from('ebkit_web_projects').insert([{ name: 'anonymous-test', repository: 'https://github.com/test/test', region: 'us-east-2', port: 8080 }]);
assert.ok(write.error, 'Anonymous users must not create projects');
console.log('PASS: anonymous project reads and writes denied');
