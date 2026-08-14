export async function waitForSpawnedBackend<T>(
  backendSpawnPromise: Promise<boolean>,
  waitForReady: () => Promise<T>,
): Promise<T | null> {
  if (!(await backendSpawnPromise)) return null;
  return waitForReady();
}

export async function startMihomoAfterBackendReady<T>(
  backendReadyPromise: Promise<boolean>,
  startMihomo: () => Promise<T>,
  canStart: () => boolean = () => true,
): Promise<T | null> {
  if (!(await backendReadyPromise) || !canStart()) return null;
  return startMihomo();
}
