import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import {
  type BackendEndpoint,
  BackendManager,
  BackendReadinessTimeoutError,
  formatBackendApiUrl,
  isExpectedBackendHealth,
  mergeStartupSettings,
  normalizeBackendEndpoint,
} from '../electron/src/backend-manager';
import { MAX_BACKEND_WAIT_MS, waitForBackendEndpoint } from '../electron/src/backend-wait';
import { MihomoManager } from '../electron/src/mihomo-manager';
import { buildProxyRules, normalizeProxyHost } from '../electron/src/proxy-endpoint';
import {
  startMihomoAfterBackendReady,
  waitForSpawnedBackend,
} from '../electron/src/startup-sequencer';

const rootDir = resolve(__dirname, '..');
const source = (path: string) => readFileSync(join(rootDir, path), 'utf8');

const mainSource = source('electron/src/main.ts');
const backendSource = source('electron/src/backend-manager.ts');
const electronConstants = source('electron/src/constants.ts');
const ipcSource = source('electron/src/ipc-handlers.ts');
const mihomoSource = source('electron/src/mihomo-manager.ts');
const windowSource = source('electron/src/window-manager.ts');
const utilsSource = source('electron/src/utils.ts');
const loggerSource = source('electron/src/logger.ts');
const appSource = source('akagi_frontend/src/App.tsx');
const exitOverlaySource = source('akagi_frontend/src/components/ExitOverlay.tsx');
const frontendConstants = source('akagi_frontend/src/config/constants.ts');
const dashboardSource = source('akagi_frontend/src/pages/Dashboard.tsx');

const backendStart = mainSource.indexOf('const backendSpawnPromise = backendManager.start()');
const backendReady = mainSource.indexOf('const backendReadyPromise = waitForSpawnedBackend');
const dashboardCreate = mainSource.indexOf('createDashboardWindow()');
const mihomoAfterReady = mainSource.indexOf('void startMihomoAfterBackendReady(');
assert.ok(backendStart >= 0, 'Backend process spawn must have an explicit promise.');
assert.ok(
  backendStart < backendReady && backendReady < dashboardCreate,
  'Backend spawn and readiness must be represented by separate promises.',
);
assert.ok(
  dashboardCreate < mihomoAfterReady,
  'The trusted dashboard must load while backend readiness continues.',
);

const localStartupLoad = appSource.slice(
  appSource.indexOf('const loadStartupConfig'),
  appSource.indexOf('const startupConfigPromise'),
);
assert.match(localStartupLoad, /getStartupConfig\(\)/);
assert.doesNotMatch(localStartupLoad, /waitForBackend|fetchSettingsApi/);
assert.match(
  appSource,
  /const backendEndpointPromise[\s\S]*waitForBackend\(60_000\)[\s\S]*const backendSettingsPromise[\s\S]*fetchSettingsApi\(\)/,
);
assert.match(appSource, /backendEndpointPromise\.then\([\s\S]*setConnection\(endpoint\)/);
assert.match(appSource, /const data = use\(startupConfigPromise\)/);
assert.match(appSource, /<GameProvider backendReady=\{backendState\.status === 'ready'\}>/);
assert.doesNotMatch(appSource, /const data = use\(backendSettingsPromise\)/);
assert.match(dashboardSource, /controlsDisabled=\{!backendReady\}/);

const prodStart = backendSource.indexOf('private async startProdBackend');
const healthStart = backendSource.indexOf('private async startHealthCheck');
const prodSource = backendSource.slice(prodStart, healthStart);
assert.ok(prodStart >= 0 && healthStart > prodStart, 'Production backend method was not found.');
assert.ok(
  prodSource.indexOf('await this.getResourceStatus()') <
    prodSource.indexOf('spawn(pythonExecutable'),
  'Packaged backend execution must follow the bounded resource availability check.',
);
assert.match(prodSource, /PYTHONDONTWRITEBYTECODE: '1'/);
assert.equal((backendSource.match(/PYTHONIOENCODING: 'utf-8'/g) ?? []).length, 2);
assert.equal((backendSource.match(/PYTHONUTF8: '1'/g) ?? []).length, 2);
assert.match(prodSource, /AKAGI_USER_DATA_DIR: getUserDataRoot\(\)/);
assert.match(backendSource, /join\(getUserDataRoot\(\), 'config', 'settings\.json'\)/);
assert.match(backendSource, /public async getStartupConfig\(\)/);
assert.match(backendSource, /mergeStartupSettings\(bundledDefaults, startupSettings\)/);
assert.match(backendSource, /getAssetPath\('assets', 'settings\.default\.json'\)/);
assert.match(mainSource, /initializeLogger\(join\(getUserDataRoot\(\), 'logs'\)\)/);
assert.match(utilsSource, /app\.getPath\('userData'\)/);
assert.match(loggerSource, /function safeConsoleWrite/);
assert.match(loggerSource, /process\.stdout\?\.on\('error', disableBrokenConsole\)/);
assert.match(loggerSource, /process\.stderr\?\.on\('error', disableBrokenConsole\)/);
assert.doesNotMatch(loggerSource, /originalConsole\.(?:log|info|warn|error|debug)\(/);

const mergedLegacySettings = mergeStartupSettings(
  {
    autoplay: {
      enabled: false,
      timing: { first_discard: { min: 2, max: 5 } },
      advanced_timing: { ron: { min: 0.3, max: 1 } },
      auto_join: { enabled: false, room: 'gold' },
    },
  },
  {
    autoplay: {
      enabled: true,
      timing: { first_tile: 3.2, rand_min: 0.3, rand_max: 2.45 },
    },
  },
);
const mergedAutoplay = mergedLegacySettings.autoplay as Record<string, unknown>;
assert.equal(mergedAutoplay.enabled, true, 'Existing autoplay values must win over defaults.');
assert.deepEqual(mergedAutoplay.auto_join, { enabled: false, room: 'gold' });
assert.deepEqual(mergedAutoplay.advanced_timing, { ron: { min: 0.3, max: 1 } });
assert.deepEqual(mergedAutoplay.timing, {
  first_discard: { min: 2, max: 5 },
  first_tile: 3.2,
  rand_min: 0.3,
  rand_max: 2.45,
});
const resourceCheckAwait = prodSource.indexOf('await this.getResourceStatus()');
const postCheckCancellation = prodSource.indexOf('if (this.isClosing)', resourceCheckAwait);
const productionSpawn = prodSource.indexOf('spawn(pythonExecutable');
const postSpawnCancellation = prodSource.indexOf('if (this.isClosing)', productionSpawn);
assert.ok(
  resourceCheckAwait < postCheckCancellation && postCheckCancellation < productionSpawn,
  'Shutdown during the resource availability check must cancel startup before spawn.',
);
assert.ok(
  postSpawnCancellation > productionSpawn,
  'A process spawned during cancellation must be detected and terminated.',
);
const stopSource = backendSource.slice(backendSource.indexOf('public async stop()'));
assert.ok(
  stopSource.indexOf('this.isClosing = true') < stopSource.indexOf('if (!this.isRunning()) return'),
  'stop() must cancel pending startup even when no child process exists yet.',
);
assert.match(stopSource, /this\.markFailed\(new Error\('Backend startup cancelled/);
assert.match(backendSource, /terminateCancelledProcess\(childProcess\)/g);
assert.match(prodSource, /if \(!resourceStatus\.lib\)[\s\S]*this\.markFailed\(new Error\(msg\)\)/);
const resourceStatusMethod = backendSource.slice(
  backendSource.indexOf('public getResourceStatus()'),
  backendSource.indexOf('public async start()'),
);
assert.match(backendSource, /resourceStatusPromise: Promise<ResourceStatus> \| null/);
assert.match(
  resourceStatusMethod,
  /if \(this\.resourceStatusPromise\) return this\.resourceStatusPromise/,
);
assert.match(resourceStatusMethod, /this\.resourceStatus = status/);
assert.match(resourceStatusMethod, /this\.resourceStatusPromise = null/);
assert.match(backendSource, /private markFailed\(error: Error\)/);
assert.match(backendSource, /if \(this\.readyError\) throw this\.readyError/);
assert.match(backendSource, /if \(timeoutId\) clearTimeout\(timeoutId\)/);
assert.match(backendSource, /readyEndpoint: BackendEndpoint \| null/);
assert.match(backendSource, /if \(this\.readyEndpoint\) return \{ \.\.\.this\.readyEndpoint \}/);
const healthSource = backendSource.slice(
  backendSource.indexOf('private async startHealthCheck'),
  backendSource.indexOf('private setupListeners'),
);
assert.match(healthSource, /formatBackendApiUrl\(endpoint, '\/api\/health'\)/);
assert.match(healthSource, /isExpectedBackendHealth\(payload, expectedPid\)/);
assert.match(healthSource, /this\.markFailed\(error\)[\s\S]*this\.terminateFailedProcess\(\)/);
assert.doesNotMatch(healthSource, /fetch\(`http:\/\/\$\{host\}:\$\{port\}`/);
const waitIpcSource = ipcSource.slice(
  ipcSource.indexOf("handle('wait-for-backend'"),
  ipcSource.indexOf("handle('update-locale'"),
);
assert.match(waitIpcSource, /return waitForBackendEndpoint\(backendManager, value\)/);
assert.doesNotMatch(waitIpcSource, /Backend failed to start/);
assert.match(ipcSource, /formatBackendApiUrl\(config, '\/api\/protocol\/update'\)/);
assert.match(electronConstants, /BACKEND_STARTUP_CHECK_INTERVAL_MS = 100/);
assert.match(electronConstants, /BACKEND_STARTUP_CHECK_MAX_INTERVAL_MS = 500/);
assert.match(electronConstants, /BACKEND_SHUTDOWN_TIMEOUT_MS = 2000/);
assert.match(electronConstants, /BACKEND_READY_TIMEOUT_MS = 60000/);
assert.equal(
  MAX_BACKEND_WAIT_MS,
  120_000,
  'The IPC compatibility limit must not be confused with the 60-second backend lifecycle.',
);
assert.match(mihomoSource, /STOP_TIMEOUT_MS = 2_000/);

assert.match(mainSource, /let shutdownPromise: Promise<void> \| null = null/);
const shutdownFunction = mainSource.slice(
  mainSource.indexOf('function shutdownOnce'),
  mainSource.indexOf("app.on('second-instance'"),
);
assert.ok(
  shutdownFunction.indexOf('mihomoManager.beginShutdown()') <
    shutdownFunction.indexOf('if (shutdownPromise)'),
  'Mihomo cancellation must be signalled synchronously before queued shutdown work.',
);
assert.match(mainSource, /shutdownStarted = true[\s\S]*mihomoManager\.stop\(\)/);
assert.ok(
  mainSource.indexOf('mihomoManager.stop()') < mainSource.indexOf('backendManager.stop()'),
  'Shutdown must stop mihomo before the backend.',
);
const shutdownHandler = ipcSource.slice(
  ipcSource.indexOf("handle('request-shutdown'"),
  ipcSource.indexOf("handle('mihomo-status'"),
);
assert.match(shutdownHandler, /Promise\.all\(\[animation, shutdownApplication\(\)\]\)/);
assert.doesNotMatch(shutdownHandler, /mihomoManager\.stop\(\)|backendManager\.stop\(\)/);
const mihomoReconcileHandler = ipcSource.slice(
  ipcSource.indexOf("handle('mihomo-reconcile'"),
  ipcSource.indexOf("handle('mihomo-stop'"),
);
assert.match(mihomoReconcileHandler, /resetGameWindowForProxyChange\(\)/);
assert.match(mihomoReconcileHandler, /requireBoolean\(gameProxyChanged/);
assert.match(mihomoReconcileHandler, /gameWindowClosed/);
const postBackendStartup = mainSource.slice(
  mainSource.indexOf('void startMihomoAfterBackendReady'),
);
assert.match(postBackendStartup, /\(\) => mihomoManager\.startIfEnabled\(\)/);
assert.match(postBackendStartup, /\(\) => !shutdownStarted/);
const guardedUpdateCheck = mainSource.slice(
  mainSource.indexOf('function checkForUpdatesIfActive'),
  mainSource.indexOf('function shutdownOnce'),
);
assert.match(guardedUpdateCheck, /if \(shutdownStarted\) return/);
assert.equal(
  (mainSource.match(/updaterManager\.checkForUpdates\(\)/g) ?? []).length,
  1,
  'Every update check must pass through the shutdown guard.',
);

const mihomoStartIndex = mihomoSource.indexOf('private async doStart()');
const mihomoStart = mihomoSource.slice(
  mihomoStartIndex,
  mihomoSource.indexOf('public async stop()', mihomoStartIndex),
);
assert.match(
  mihomoSource,
  /public beginShutdown\(\): void[\s\S]*this\.shutdownController\.abort\(\)/,
);
assert.match(mihomoStart, /validateConfig\([\s\S]*this\.shutdownController\.signal/);
assert.match(mihomoStart, /launchMihomoDirectly\([\s\S]*signal: this\.shutdownController\.signal/);
assert.match(
  mihomoStart,
  /this\.session = launchedSession;[\s\S]*if \(this\.isClosing\)[\s\S]*stopSession\(launchedSession\)/,
);
assert.match(mihomoSource, /while \(Date\.now\(\) < deadline\) \{[\s\S]*assertStartupAllowed\(\)/);
assert.doesNotMatch(mihomoSource, /launchWindowsTunHelper|UAC approval|权限助手/);
assert.match(mihomoSource, /child\.kill\('SIGKILL'\)/);

assert.doesNotMatch(appSource, /APP_SPLASH_SHOW_MS/);
assert.match(appSource, /duration-300/);
assert.match(frontendConstants, /APP_SPLASH_EXIT_MS = 300/);
assert.match(dashboardSource, /transition duration-300/);
assert.match(exitOverlaySource, /duration-300/);
assert.match(electronConstants, /EXIT_ANIMATION_DELAY_MS = 300/);

const dashboardMethod = windowSource.slice(
  windowSource.indexOf('public async createDashboardWindow'),
  windowSource.indexOf('public async toggleHudWindow'),
);
const hudToggleMethod = windowSource.slice(
  windowSource.indexOf('public async toggleHudWindow'),
  windowSource.indexOf('private async createHudWindow'),
);
assert.doesNotMatch(dashboardMethod, /createHudWindow\(/);
assert.match(hudToggleMethod, /if \(!this\.hudWindow\)[\s\S]*await this\.createHudWindow\(\)/);

const gameWindowMethod = windowSource.slice(windowSource.indexOf('public async createGameWindow'));
const directProxy = gameWindowMethod.indexOf("await gameSession.setProxy({ mode: 'direct' })");
const fixedProxy = gameWindowMethod.indexOf("mode: 'fixed_servers'");
const connectionReset = gameWindowMethod.indexOf('await gameSession.closeAllConnections()');
const handlerAttach = gameWindowMethod.indexOf('this.gameHandler = new GameHandler');
assert.ok(
  directProxy >= 0 && directProxy < handlerAttach,
  'Direct proxy reset must precede GameHandler.',
);
assert.ok(
  fixedProxy >= 0 && directProxy < connectionReset && fixedProxy < connectionReset,
  'Proxy changes must close pooled connections before game traffic starts.',
);
assert.ok(
  connectionReset < handlerAttach && connectionReset < gameWindowMethod.indexOf('loadURL(url)'),
);
const proxyFailureHandler = gameWindowMethod.slice(
  gameWindowMethod.indexOf("logger.error('Failed to initialize game proxy:'"),
  handlerAttach,
);
assert.match(proxyFailureHandler, /setProxy\(\{ mode: 'direct' \}\)/);
assert.match(proxyFailureHandler, /this\.gameWindow\.destroy\(\)/);
assert.match(proxyFailureHandler, /this\.gameWindow = null/);

assert.equal(normalizeProxyHost('*'), '127.0.0.1');
assert.equal(normalizeProxyHost('0.0.0.0'), '127.0.0.1');
assert.equal(normalizeProxyHost('::'), '127.0.0.1');
assert.equal(normalizeProxyHost('[::]'), '127.0.0.1');
assert.equal(normalizeProxyHost('::1'), '[::1]');
assert.equal(normalizeProxyHost('[2001:db8::1]'), '[2001:db8::1]');
assert.equal(buildProxyRules('::1', 6789), 'http://[::1]:6789');
assert.equal(buildProxyRules('mitm.local', 6789), 'http://mitm.local:6789');
assert.throws(() => buildProxyRules('http://attacker.invalid', 6789), /Invalid MITM proxy host/);
assert.throws(() => buildProxyRules('[::1', 6789), /Invalid MITM proxy host/);
assert.throws(() => buildProxyRules('127.0.0.1', 0), /Invalid MITM proxy port/);
assert.deepEqual(normalizeBackendEndpoint({ host: '0.0.0.0', port: 8765 }), {
  host: '127.0.0.1',
  port: 8765,
});
assert.deepEqual(normalizeBackendEndpoint({ host: '::', port: 8765 }), {
  host: '[::1]',
  port: 8765,
});
assert.deepEqual(normalizeBackendEndpoint({ host: '::1', port: 8765 }), {
  host: '[::1]',
  port: 8765,
});
assert.equal(
  formatBackendApiUrl({ host: '::1', port: 8765 }, '/api/health'),
  'http://[::1]:8765/api/health',
);
assert.equal(
  isExpectedBackendHealth(
    {
      ok: true,
      data: { service: 'akagi-ng', status: 'ready', version: 'test', pid: 1234 },
    },
    1234,
  ),
  true,
);
assert.equal(
  isExpectedBackendHealth(
    { ok: true, data: { service: 'akagi-ng', status: 'ready', pid: 9999 } },
    1234,
  ),
  false,
  'A stale backend process must not satisfy readiness for a newly spawned PID.',
);
assert.equal(
  isExpectedBackendHealth(
    { ok: true, data: { service: 'akagi-ng', status: 'ready', pid: 1234 } },
    1234,
  ),
  false,
  'A legacy or spoofed health response without a version must be rejected.',
);
assert.equal(
  isExpectedBackendHealth(
    { ok: true, data: { service: 'other', status: 'ready', pid: 1234 } },
    1234,
  ),
  false,
);

function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timeoutId = setTimeout(
      () => reject(new Error('Mihomo cancellation timed out.')),
      timeoutMs,
    );
    promise.then(
      (value) => {
        clearTimeout(timeoutId);
        resolve(value);
      },
      (error) => {
        clearTimeout(timeoutId);
        reject(error);
      },
    );
  });
}

async function testMihomoShutdownCancellation(): Promise<void> {
  const manager = new MihomoManager({} as never);
  let running = true;
  let stopCalls = 0;
  const session = {
    isRunning: () => running,
    stop: async () => {
      stopCalls += 1;
      running = false;
    },
  };
  Object.assign(manager, { session });

  manager.beginShutdown();
  await withTimeout(manager.stop(), 250);
  assert.equal(stopCalls, 1, 'Shutdown and queued stop must share one session stop operation.');
  assert.equal(manager.isRunning(), false, 'Shutdown must not retain an active TUN session.');
  assert.deepEqual(await manager.startIfEnabled(), { enabled: false, running: false });
  assert.deepEqual(await manager.reconcile(), { enabled: false, running: false });

  let resolveConfig!: (value: {
    mitm: { enabled: boolean; host: string; port: number };
    mihomo: {
      enabled: boolean;
      mixedPort: number;
      controllerPort: number;
      strictRoute: boolean;
    };
  }) => void;
  const configPending = new Promise<Parameters<typeof resolveConfig>[0]>((resolve) => {
    resolveConfig = resolve;
  });
  const pendingManager = new MihomoManager({ getProxyConfig: () => configPending } as never);
  const pendingStart = pendingManager.startIfEnabled();
  await Promise.resolve();
  pendingManager.beginShutdown();
  resolveConfig({
    mitm: { enabled: true, host: '127.0.0.1', port: 6789 },
    mihomo: {
      enabled: true,
      mixedPort: 7890,
      controllerPort: 9090,
      strictRoute: false,
    },
  });
  const [, pendingStatus] = await withTimeout(
    Promise.all([pendingManager.stop(), pendingStart]),
    250,
  );
  assert.deepEqual(pendingStatus, { enabled: false, running: false });
  assert.equal(pendingManager.isRunning(), false);
}

async function testMihomoSuspendsInBrowserModeAndRestoresInProxyMode(): Promise<void> {
  const config = {
    mitm: { enabled: false, host: '127.0.0.1', port: 6789 },
    mihomo: {
      enabled: true,
      mixedPort: 7890,
      controllerPort: 9090,
      strictRoute: false,
    },
  };
  const manager = new MihomoManager({ getProxyConfig: async () => config } as never);
  let running = true;
  let stopCalls = 0;
  let startCalls = 0;
  Object.assign(manager, {
    session: {
      isRunning: () => running,
      stop: async () => {
        stopCalls += 1;
        running = false;
      },
    },
    doStart: async () => {
      startCalls += 1;
      return { enabled: true, running: true };
    },
  });

  assert.deepEqual(await manager.reconcile(), { enabled: true, running: false });
  assert.equal(stopCalls, 1, 'Switching to the built-in browser must stop the active TUN.');
  assert.equal(startCalls, 0, 'Browser mode must not restart the remembered TUN preference.');

  config.mitm.enabled = true;
  assert.deepEqual(await manager.reconcile(), { enabled: true, running: true });
  assert.equal(startCalls, 1, 'Switching back to external proxy mode must restore TUN.');
}

async function testResourceAvailabilitySingleFlight(): Promise<void> {
  const manager = Object.create(BackendManager.prototype) as BackendManager;
  let checkCalls = 0;
  let resolveCheck!: (value: unknown) => void;
  const checkPending = new Promise<unknown>((resolve) => {
    resolveCheck = resolve;
  });
  const status = {
    lib: true,
    models: true,
    missingCritical: [],
    missingOptional: [],
  };
  Object.assign(manager, {
    resourceStatus: null,
    resourceStatusPromise: null,
    resourceChecker: {
      check: () => {
        checkCalls += 1;
        return checkPending;
      },
    },
  });

  const firstCheck = manager.getResourceStatus();
  const secondCheck = manager.getResourceStatus();
  assert.equal(firstCheck, secondCheck, 'Concurrent resource checks must share one promise.');
  await Promise.resolve();
  assert.equal(checkCalls, 1);
  resolveCheck(status);
  assert.deepEqual(await firstCheck, status);
  assert.deepEqual(await secondCheck, status);
  assert.deepEqual(await manager.getResourceStatus(), status);
  assert.equal(checkCalls, 1, 'A successful resource check must remain cached.');

  let retryCalls = 0;
  Object.assign(manager, {
    resourceStatus: null,
    resourceStatusPromise: null,
    resourceChecker: {
      check: async () => {
        retryCalls += 1;
        if (retryCalls === 1) throw new Error('synthetic resource check failure');
        return status;
      },
    },
  });
  await assert.rejects(manager.getResourceStatus(), /synthetic resource check failure/);
  assert.deepEqual(await manager.getResourceStatus(), status);
  assert.equal(retryCalls, 2, 'A failed resource check must clear the in-flight cache for retry.');
}

interface BackendManagerTestInternals {
  appendStderr(chunk: string): void;
  buildReadinessError(message: string): Error;
  markFailed(error: Error): void;
  markReady(endpoint: BackendEndpoint): void;
}

function createReadinessManager(): BackendManager {
  const manager = Object.create(BackendManager.prototype) as BackendManager;
  let resolveReady!: (endpoint: BackendEndpoint) => void;
  let rejectReady!: (error: Error) => void;
  const readyPromise = new Promise<BackendEndpoint>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  void readyPromise.catch(() => {});
  Object.assign(manager, {
    isReadyState: false,
    readyEndpoint: null,
    readyError: null,
    readyPromise,
    resolveReady,
    rejectReady,
    isClosing: false,
    startupStartedAt: Date.now(),
    lastHealthEndpoint: null,
    lastHealthError: null,
    stderrTail: '',
  });
  return manager;
}

async function testBackendReadinessErrorPropagation(): Promise<void> {
  const manager = createReadinessManager();
  const internals = manager as unknown as BackendManagerTestInternals;
  const startupFailure = new Error('bind failed: winerror=10013');
  const waiting = manager.waitForReady(5_000);
  internals.markFailed(startupFailure);
  await assert.rejects(
    waiting,
    (error: unknown) => error === startupFailure,
    'A concrete startup failure must settle waiters immediately and preserve Error identity.',
  );

  const timeoutManager = createReadinessManager();
  await assert.rejects(
    timeoutManager.waitForReady(1),
    (error: unknown) => error instanceof BackendReadinessTimeoutError,
  );
  const timeoutInternals = timeoutManager as unknown as BackendManagerTestInternals;
  timeoutInternals.markReady({ host: '::1', port: 18765 });
  assert.deepEqual(await timeoutManager.waitForReady(), { host: '[::1]', port: 18765 });
  assert.deepEqual(await timeoutManager.getBackendConfig(), { host: '[::1]', port: 18765 });

  const diagnosticManager = createReadinessManager();
  const diagnosticInternals = diagnosticManager as unknown as BackendManagerTestInternals;
  Object.assign(diagnosticManager, {
    lastHealthEndpoint: { host: '127.0.0.1', port: 8765 },
    lastHealthError: new Error('connect ECONNREFUSED'),
  });
  diagnosticInternals.appendStderr(`${'x'.repeat(9_000)}\ntraceback marker`);
  const diagnostic = diagnosticInternals.buildReadinessError('backend exited').message;
  assert.match(diagnostic, /127\.0\.0\.1:8765\/api\/health/);
  assert.match(diagnostic, /connect ECONNREFUSED/);
  assert.match(diagnostic, /traceback marker/);
  assert.ok(diagnostic.length < 8_500, 'The stderr diagnostic tail must remain bounded.');
}

async function testBackendWaitIpcContract(): Promise<void> {
  const endpoint = { host: '127.0.0.1', port: 18765 };
  let receivedTimeout: number | undefined;
  assert.deepEqual(
    await waitForBackendEndpoint(
      {
        waitForReady: async (timeoutMs) => {
          receivedTimeout = timeoutMs;
          return endpoint;
        },
      },
      12_345,
    ),
    endpoint,
  );
  assert.equal(receivedTimeout, 12_345);

  const readinessFailure = new Error('specific backend readiness failure');
  await assert.rejects(
    waitForBackendEndpoint(
      {
        waitForReady: async () => {
          throw readinessFailure;
        },
      },
      1_000,
    ),
    (error: unknown) => error === readinessFailure,
    'The IPC adapter must not replace the original readiness error.',
  );
  await assert.rejects(
    waitForBackendEndpoint({ waitForReady: async () => endpoint }, MAX_BACKEND_WAIT_MS + 1),
    /Invalid backend wait timeout/,
  );
}

async function testBackendAndMihomoStartupSequencing(): Promise<void> {
  let waitCalls = 0;
  assert.equal(
    await waitForSpawnedBackend(Promise.resolve(false), async () => {
      waitCalls += 1;
      return { host: '127.0.0.1', port: 8765 };
    }),
    null,
  );
  assert.equal(waitCalls, 0, 'A backend that failed to spawn must not enter readiness waiting.');

  let resolveReady!: (ready: boolean) => void;
  const backendReady = new Promise<boolean>((resolve) => {
    resolveReady = resolve;
  });
  let mihomoStarts = 0;
  const mihomoStart = startMihomoAfterBackendReady(backendReady, async () => {
    mihomoStarts += 1;
    return 'started';
  });
  await Promise.resolve();
  assert.equal(mihomoStarts, 0, 'Mihomo must not start while backend readiness is pending.');
  resolveReady(true);
  assert.equal(await mihomoStart, 'started');
  assert.equal(mihomoStarts, 1);

  assert.equal(
    await startMihomoAfterBackendReady(
      Promise.resolve(true),
      async () => {
        mihomoStarts += 1;
        return 'started during shutdown';
      },
      () => false,
    ),
    null,
  );
  assert.equal(mihomoStarts, 1, 'Shutdown must prevent queued Mihomo startup.');

  const readinessFailure = new Error('backend exited before ready');
  let rejectedMihomoStarts = 0;
  await assert.rejects(
    startMihomoAfterBackendReady(Promise.reject(readinessFailure), async () => {
      rejectedMihomoStarts += 1;
      return 'unexpected';
    }),
    (error: unknown) => error === readinessFailure,
  );
  assert.equal(rejectedMihomoStarts, 0, 'A rejected readiness promise must not start Mihomo.');
}

Promise.all([
  testMihomoShutdownCancellation(),
  testMihomoSuspendsInBrowserModeAndRestoresInProxyMode(),
  testResourceAvailabilitySingleFlight(),
  testBackendReadinessErrorPropagation(),
  testBackendWaitIpcContract(),
  testBackendAndMihomoStartupSequencing(),
]).then(
  () => console.log('Lifecycle and proxy regression tests passed.'),
  (error) => {
    console.error(error);
    process.exitCode = 1;
  },
);
