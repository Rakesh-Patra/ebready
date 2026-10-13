import {beforeEach, describe, expect, it, vi } from 'vitest';
const auth=vi.hoisted(()=>({getCurrentUser:vi.fn(),getValidAccessToken:vi.fn()}));
vi.mock('@insforge/sdk',()=>({createClient:()=>({auth:{getCurrentUser:auth.getCurrentUser},getHttpClient:()=>({getValidAccessToken:auth.getValidAccessToken})})}));
beforeEach(()=>{vi.clearAllMocks();auth.getCurrentUser.mockResolvedValue({data:{user:null},error:null});vi.stubGlobal('fetch',vi.fn());});
import {backend,uploadZip,downloadPreparedZip, parseEnv,quoteEnvValues, parseRepository } from './lib';

describe('repository validation', () => {
  it('normalizes GitHub clone URLs', () => {
    expect(parseRepository(' https://github.com/Rakesh-Patra/devboard.git/ ').url).toBe('https://github.com/Rakesh-Patra/devboard');
  });
  it.each(['http://github.com/a/b', 'https://github.com.evil.com/a/b', 'https://github.com/a/b/tree/main', 'https://github.com/a/b?token=secret', 'git@github.com:a/b.git'])('rejects unsafe or unsupported URL %s', input => {
    expect(() => parseRepository(input)).toThrow();
  });
});
describe('local .env import', () => {
  it('extracts names from blank, exported and quoted settings', () => {
    expect(Object.keys(parseEnv('# Comment\nDATABASE_URL=\nexport API_KEY="private"\r\nCACHE_URL=redis://localhost'))).toEqual(['DATABASE_URL', 'API_KEY', 'CACHE_URL']);
  });
  it('rejects malformed names and lines', () => {
    expect(() => parseEnv('BAD-NAME=secret')).toThrow();
    expect(() => parseEnv('secret without a name')).toThrow();
  });
});

describe('cloud session enforcement',()=>{
 it('does not send protected requests after sign-out',async()=>{
  await expect(backend('/api/deploy',{})).rejects.toThrow('Log in');
  await expect(uploadZip({name:'project.zip'} as File)).rejects.toThrow('Log in');
  await expect(downloadPreparedZip('upload','hash')).rejects.toThrow('Log in');
  expect(fetch).not.toHaveBeenCalled();expect(auth.getValidAccessToken).not.toHaveBeenCalled();
 });
 it('rejects a cached user without an access token before contacting the backend',async()=>{
  auth.getCurrentUser.mockResolvedValue({data:{user:{id:'owner'}},error:null});auth.getValidAccessToken.mockResolvedValue(null);
  await expect(backend('/api/projects/owned/deploy',{})).rejects.toThrow('session has ended');expect(fetch).not.toHaveBeenCalled();
 });
});

it('quotes pasted database URLs without changing their query parameters or quoting twice',()=>{
 const url='postgresql://db.example.test/neondb?sslmode=require&channel_binding=require';
 const formatted=quoteEnvValues('POSTGRES_URL='+url+'\nPORT=8080\nEMPTY=');
 expect(formatted).toBe('POSTGRES_URL="'+url+'"\nPORT=8080\nEMPTY=');
 expect(parseEnv(formatted).POSTGRES_URL).toBe(url);expect(quoteEnvValues(formatted)).toBe(formatted);
 expect(quoteEnvValues('NOTE="already quoted"\n# comment')).toBe('NOTE="already quoted"\n# comment');
});
