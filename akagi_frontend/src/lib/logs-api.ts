import { fetchJson } from '@/lib/api-client';
import type { HttpCapture } from '@/types';

export function fetchLogTail(limit = 500): Promise<string[]> {
  return fetchJson<string[]>(`/api/logs/tail?limit=${limit}`);
}

export function fetchHttpCaptures(limit = 200): Promise<HttpCapture[]> {
  return fetchJson<HttpCapture[]>(`/api/http-captures?limit=${limit}`);
}

export function clearHttpCaptures(): Promise<void> {
  return fetchJson<void>('/api/http-captures', { method: 'DELETE' });
}
