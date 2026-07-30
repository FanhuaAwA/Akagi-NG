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
  const mainWindow = windowManager.getMainWindow();
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
  } else {
    windowManager.createDashboardWindow();
  }
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

  // 1. Start Python Backend
  backendManager.start();
  const mihomoStatus = await mihomoManager.startIfEnabled();
  if (mihomoStatus.error) {
    logger.error(`mihomo initialization failed: ${mihomoStatus.error}`);
    dialog.showErrorBox('Mihomo Initialization Failed', mihomoStatus.error);
  }

  // 2. Setup Tray
  windowManager.setupTray(() => updaterManager.checkForUpdates());

  // 3. Create Dashboard Window
  windowManager.createDashboardWindow();

  // 4. Setup Auto Updater
  updaterManager.checkForUpdates();

  app.on('activate', () => {
    windowManager.createDashboardWindow();
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

  if (backendManager.isRunning() || mihomoManager.isRunning()) {
    event.preventDefault();
    isQuitting = true;

    try {
      await Promise.all([backendManager.stop(), mihomoManager.stop()]);
    } catch (err) {
      logger.error('Error during shutdown:', err);
    } finally {
      app.quit();
    }
  }
});
