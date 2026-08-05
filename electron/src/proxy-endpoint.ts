import { isIP } from 'node:net';

const WILDCARD_BIND_HOSTS = new Set(['*', '0.0.0.0', '::', '::0', '0:0:0:0:0:0:0:0']);

function unwrapIpv6Brackets(host: string): string {
  const hasOpeningBracket = host.startsWith('[');
  const hasClosingBracket = host.endsWith(']');
  if (hasOpeningBracket !== hasClosingBracket) {
    throw new Error(`Invalid MITM proxy host: ${host}`);
  }
  return hasOpeningBracket ? host.slice(1, -1) : host;
}

function isValidHostname(host: string): boolean {
  if (host.length === 0 || host.length > 253 || host.endsWith('.')) return false;
  return host.split('.').every((label) => {
    if (label.length === 0 || label.length > 63) return false;
    return /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label);
  });
}

export function normalizeProxyHost(host: string): string {
  const candidate = unwrapIpv6Brackets(host.trim());
  if (WILDCARD_BIND_HOSTS.has(candidate.toLowerCase())) return '127.0.0.1';

  const ipVersion = isIP(candidate);
  if (ipVersion === 6) return `[${candidate}]`;
  if (ipVersion === 4 || isValidHostname(candidate)) return candidate;

  throw new Error(`Invalid MITM proxy host: ${host}`);
}

export function buildProxyRules(host: string, port: number): string {
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`Invalid MITM proxy port: ${port}`);
  }
  return `http://${normalizeProxyHost(host)}:${port}`;
}
