import os from 'node:os';
import { join } from 'node:path';

import { app, dialog } from 'electron';

import { BackendManager } from './backend-manager.js';
import { registerIpcHandlers } from './ipc-handlers.js';
import { createLogger, initializeLogger } from './logger.js';
import { MihomoManager } from './mihomo-manager.js';
import { UpdaterManager } from './updater.js';
import { getProjectRoot } from './utils.js';
import { WindowManager } from './window-manager.js';

// Single Instance Lock
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.exit(0);
}

initializeLogger(join(getProjectRoot(), 'logs'));

const logger = createLogger('Main');

logger.info(`Starting Akagi-NG Desktop v${app.getVersion()}...`);
logger.info(`System: ${os.type()} ${os.release()} (${os.arch()})`);
logger.info(`Node.js: ${process.versions.node} | Electron: ${process.versions.electron}`);

const backendManager = new BackendManager();
const mihomoManager = new MihomoManager(backendManager);
const windowManager = new WindowManager(backendManager);
const updaterManager = new UpdaterManager(windowManager);

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
  registerIpcHandlers(windowManager, backendManager, mihomoManager);

  // 1. Start the unprivileged Python backend.
  backendManager.start();

  // 2. Render the dashboard before any optional UAC interaction.
  await windowManager.reconcileDesktopSettings(() => updaterManager.checkForUpdates());
  windowManager.createDashboardWindow();

  // 3. Start optional TUN asynchronously. UAC denial must not block the app/backend.
  void mihomoManager.startIfEnabled().then((mihomoStatus) => {
    if (mihomoStatus.error) {
      logger.error(`mihomo initialization failed: ${mihomoStatus.error}`);
      dialog.showErrorBox('Mihomo Initialization Failed', mihomoStatus.error);
    }
  });

  // 4. Setup Auto Updater
  updaterManager.checkForUpdates();

  app.on('activate', () => {
    windowManager.showDashboard();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

let isQuitting = false;

app.on('before-quit', async (event) => {
  windowManager.setQuitting(true);
  if (isQuitting) return;

  event.preventDefault();
  isQuitting = true;

  try {
    await mihomoManager.stop();
    await backendManager.stop();
    await windowManager.shutdown();
  } catch (err) {
    logger.error('Error during shutdown:', err);
  } finally {
    app.quit();
  }
});
