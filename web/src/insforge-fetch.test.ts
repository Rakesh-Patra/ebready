import {afterEach, describe, expect, it, vi} from 'vitest';
import {createClient} from '@insforge/sdk';
import {createInsforgeFetch} from './insforge-fetch';
afterEach(() => vi.unstubAllGlobals());
const origin = 'https://dashboard.example';
describe('same-origin InsForge transport', () => {
  it('routes the real SDK sign-in through the proxy despite absolute API paths', async () => {
    const send = vi.fn().mockResolvedValue(new Response(JSON.stringify({error:'INVALID_CREDENTIALS', message:'Invalid credentials'}), {status:401,headers:{'Content-Type':'application/json'}}));
    vi.stubGlobal('fetch', send);
    const client = createClient({baseUrl:origin, fetch:createInsforgeFetch(origin), retryCount:0});
    await client.auth.signInWithPassword({email:'transport@example.invalid',password:'synthetic-test-input'});
    expect(String(send.mock.calls[0][0])).toContain(origin + '/insforge/api/auth/sessions');
    expect(send.mock.calls[0][1].method).toBe('POST');
  });
  it('preserves database queries and request credentials', async () => {
    const send=vi.fn().mockResolvedValue(new Response('{}'));vi.stubGlobal('fetch',send);
    const init={credentials:'include' as const,headers:{Authorization:'Bearer synthetic'}};
    await createInsforgeFetch(origin)(origin+'/api/database/records/projects?limit=1',init);
    expect(String(send.mock.calls[0][0])).toBe(origin+'/insforge/api/database/records/projects?limit=1');
    expect(send.mock.calls[0][1]).toBe(init);
  });
  it('does not rewrite external URLs, application API routes or existing proxy paths', async () => {
    const send=vi.fn().mockResolvedValue(new Response('{}'));vi.stubGlobal('fetch',send);
    for(const path of ['https://other.example/api/auth/sessions',origin+'/api/deploy',origin+'/insforge/api/auth/sessions']) {
      await createInsforgeFetch(origin)(path);expect(send.mock.calls.at(-1)?.[0]).toBe(path);
    }
  });
});
