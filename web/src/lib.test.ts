import { describe, expect, it, vi } from 'vitest';
vi.mock('@insforge/sdk', () => ({ createClient: () => ({}) }));
import { parseEnv, parseRepository } from './lib';

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
