import { app, BrowserWindow, dialog } from 'electron';

import { BackendManager } from './backend-manager.js';
import { registerIpcHandlers } from './ipc-handlers.js';
import { UpdaterManager } from './updater.js';
import { WindowManager } from './window-manager.js';

const backendManager = new BackendManager();
const windowManager = new WindowManager(backendManager);
const updaterManager = new UpdaterManager(windowManager);

process.on('uncaughtException', (error) => {
  console.error('[Main] Uncaught Exception:', error);
  dialog.showErrorBox('Main Process Crash', error.message || String(error));
});

process.on('unhandledRejection', (reason) => {
  console.error('[Main] Unhandled Rejection:', reason);
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
      console.error('[Main] Error during shutdown:', err);
    } finally {
      app.quit();
    }
  }
});
