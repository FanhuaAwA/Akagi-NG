import { fetchJson } from '@/lib/api-client';
import type { FlyAQuota, OT3Health, OT3ModelInfo } from '@/types';

export type FlyAConnection = {
  server: string;
  proxy?: string;
};

async function post<T>(path: string, body: object): Promise<T> {
  return fetchJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function checkFlyAHealth(connection: FlyAConnection): Promise<OT3Health> {
  return post('/api/flya/health', connection);
}

export function fetchFlyAQuota(connection: FlyAConnection): Promise<FlyAQuota> {
  return post('/api/flya/quota', connection);
}

export function fetchFlyAModels(connection: FlyAConnection): Promise<OT3ModelInfo[]> {
  return post('/api/flya/models', connection);
}
