import os from 'node:os';
import { join } from 'node:path';

import { app, dialog } from 'electron';

import { BackendManager } from './backend-manager.js';
import { registerIpcHandlers } from './ipc-handlers.js';
import { createLogger, initializeLogger } from './logger.js';
import { MihomoManager } from './mihomo-manager.js';
import { UpdaterManager } from './updater.js';
import { getUserDataRoot } from './utils.js';
import { WindowManager } from './window-manager.js';

// Single Instance Lock
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.exit(0);
}

initializeLogger(join(getUserDataRoot(), 'logs'));

const logger = createLogger('Main');

logger.info(`Starting Akagi-NG Desktop v${app.getVersion()}...`);
logger.info(`System: ${os.type()} ${os.release()} (${os.arch()})`);
logger.info(`Node.js: ${process.versions.node} | Electron: ${process.versions.electron}`);

const backendManager = new BackendManager();
const mihomoManager = new MihomoManager(backendManager);
const windowManager = new WindowManager(backendManager);
const updaterManager = new UpdaterManager(windowManager);

let shutdownPromise: Promise<void> | null = null;
let shutdownCompleted = false;
let shutdownStarted = false;

function checkForUpdatesIfActive(): void {
  if (shutdownStarted) return;
  updaterManager.checkForUpdates();
}

function shutdownOnce(): Promise<void> {
  mihomoManager.beginShutdown();
  if (shutdownPromise) return shutdownPromise;

  shutdownStarted = true;
  windowManager.setQuitting(true);
  shutdownPromise = (async () => {
    await mihomoManager.stop().catch((error: unknown) => {
      logger.error('mihomo stop error:', error);
    });
    await backendManager.stop().catch((error: unknown) => {
      logger.error('Backend stop error:', error);
    });
    await windowManager.shutdown().catch((error: unknown) => {
      logger.error('Window stop error:', error);
    });
  })().finally(() => {
    shutdownCompleted = true;
  });
  return shutdownPromise;
}

app.on('second-instance', () => {
  logger.info('Second instance detected. Focusing existing window...');
  windowManager.showDashboard();
});

process.on('uncaughtException', (error) => {
  logger.error('Uncaught Exception:', error);
  dialog.showErrorBox('Main Process Crash', error.message || String(error));
});

process.on('unhandledRejection', (reason) => {
  logger.error('Unhandled Rejection:', reason);
});

app.whenReady().then(async () => {
  // 0. Register all IPC handlers
  registerIpcHandlers(windowManager, backendManager, mihomoManager, shutdownOnce);

  // 1. Begin protected-resource verification while the trusted local renderer loads.
  // BackendManager still gates every packaged external process on successful verification.
  const backendStartPromise = backendManager.start();

  // 2. Render the dashboard before verification, backend imports, or optional UAC can delay it.
  await windowManager.reconcileDesktopSettings(checkForUpdatesIfActive);
  if (shutdownStarted) return;
  void windowManager.createDashboardWindow();

  const backendStarted = await backendStartPromise;
  if (shutdownStarted) return;

  // 3. Start optional TUN asynchronously. UAC denial must not block the app/backend.
  if (backendStarted) {
    void mihomoManager.startIfEnabled().then((mihomoStatus) => {
      if (shutdownStarted) return;
      if (mihomoStatus.error) {
        logger.error(`mihomo initialization failed: ${mihomoStatus.error}`);
        dialog.showErrorBox('Mihomo Initialization Failed', mihomoStatus.error);
      }
    });
  } else {
    logger.error('External runtime startup was blocked. Optional mihomo startup was skipped.');
  }

  // 4. Setup Auto Updater
  checkForUpdatesIfActive();

  app.on('activate', () => {
    if (!shutdownStarted) windowManager.showDashboard();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

let quitAfterShutdownScheduled = false;

app.on('before-quit', (event) => {
  windowManager.setQuitting(true);
  if (shutdownCompleted) return;

  event.preventDefault();
  if (quitAfterShutdownScheduled) return;
  quitAfterShutdownScheduled = true;
  void shutdownOnce().finally(() => app.quit());
});
