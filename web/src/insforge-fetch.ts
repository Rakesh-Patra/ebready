// InsForge resolves absolute /api paths, which discard a base URL path prefix.
// Rewrite only this SDK transport's same-origin auth/database requests.
export function createInsforgeFetch(origin: string): typeof fetch {
  return (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), origin);
    if (url.origin === origin && /^\/api\/(auth|database)(\/|$)/.test(url.pathname)) {
      url.pathname = '/insforge' + url.pathname;
      input = input instanceof Request ? new Request(url, input) : url;
    }
    return globalThis.fetch(input, init);
  };
}
