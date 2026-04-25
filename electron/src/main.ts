import os from 'node:os';
import { join } from 'node:path';

import { app, BrowserWindow, dialog } from 'electron';

import { BackendManager } from './backend-manager.js';
import { registerIpcHandlers } from './ipc-handlers.js';
import { createLogger, initializeLogger } from './logger.js';
import { UpdaterManager } from './updater.js';
import { getProjectRoot } from './utils.js';
import { WindowManager } from './window-manager.js';

// 初始化全局日志系统（拦截所有的 console.*）
initializeLogger(join(getProjectRoot(), 'logs'));

const logger = createLogger('Main');

logger.info(`Starting Akagi-NG Desktop v${app.getVersion()}...`);
logger.info(`System: ${os.type()} ${os.release()} (${os.arch()})`);
logger.info(`Node.js: ${process.versions.node} | Electron: ${process.versions.electron}`);

const backendManager = new BackendManager();
const windowManager = new WindowManager(backendManager);
const updaterManager = new UpdaterManager(windowManager);

process.on('uncaughtException', (error) => {
  logger.error('Uncaught Exception:', error);
  dialog.showErrorBox('Main Process Crash', error.message || String(error));
});

process.on('unhandledRejection', (reason) => {
  logger.error('Unhandled Rejection:', reason);
});

app.whenReady().then(async () => {
  // 0. Register all IPC handlers
  registerIpcHandlers(windowManager, backendManager);

  // 1. Start Python Backend
  backendManager.start();

  // 2. Create Dashboard Window
  windowManager.createDashboardWindow();

  // 3. Setup Auto Updater
  updaterManager.checkForUpdates();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      windowManager.createDashboardWindow();
    }
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

  if (backendManager.isRunning()) {
    event.preventDefault();
    isQuitting = true;

    try {
      await backendManager.stop();
    } catch (err) {
      logger.error('Error during shutdown:', err);
    } finally {
      app.quit();
    }
  }
});
