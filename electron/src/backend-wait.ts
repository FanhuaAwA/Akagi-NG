import type { BackendEndpoint } from './backend-manager.js';

// Preserve the preload API's historical validation range. The manager still
// publishes its single global startup failure at BACKEND_READY_TIMEOUT_MS.
export const MAX_BACKEND_WAIT_MS = 120_000;

export interface BackendReadinessProvider {
  waitForReady(timeoutMs?: number): Promise<BackendEndpoint>;
}

export async function waitForBackendEndpoint(
  backend: BackendReadinessProvider,
  value?: unknown,
): Promise<BackendEndpoint> {
  const timeoutMs = value === undefined ? undefined : Number(value);
  if (
    timeoutMs !== undefined &&
    (!Number.isFinite(timeoutMs) || timeoutMs < 0 || timeoutMs > MAX_BACKEND_WAIT_MS)
  ) {
    throw new TypeError('Invalid backend wait timeout.');
  }

  // Do not translate failures here. Electron IPC preserves this message for
  // the renderer, including the endpoint, native backend error, and stderr tail.
  return backend.waitForReady(timeoutMs);
}
