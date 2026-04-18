import { app, BrowserWindow, ipcMain, shell } from 'electron';

import type { BackendManager } from './backend-manager.js';
import { EXIT_ANIMATION_DELAY_MS } from './constants.js';
import { isSafeWindow, safeSend } from './utils.js';
import type { WindowManager } from './window-manager.js';

export function registerIpcHandlers(windowManager: WindowManager, backendManager: BackendManager) {
  // Toggle HUD Window
  ipcMain.handle('toggle-hud', async (_event, show: boolean) => {
    await windowManager.toggleHudWindow(show);
    return true;
  });

  // Start Game Window / Browser Mode
  ipcMain.handle(
    'start-game',
    async (_event, options: { url?: string; useMitm?: boolean; platform?: string }) => {
      await windowManager.createGameWindow(options);
      return true;
    },
  );

  // Request App Shutdown
  ipcMain.handle('request-shutdown', async () => {
    // Notify all windows to start exit animation
    safeSend(windowManager.getMainWindow(), 'exit-animation-start');
    safeSend(windowManager.getGameWindow(), 'exit-animation-start');

    // Run exit animation and backend shutdown in parallel
    await Promise.all([
      new Promise((resolve) => setTimeout(resolve, EXIT_ANIMATION_DELAY_MS)),
      backendManager
        .stop()
        .catch((err: unknown) => console.error('[IPC] Backend stop error:', err)),
    ]);

    app.quit();
    return true;
  });

  // Open external URL in system browser
  ipcMain.handle('open-external', async (_event, url: string) => {
    try {
      const parsed = new URL(url);
      if (['http:', 'https:'].includes(parsed.protocol)) {
        await shell.openExternal(url);
        return true;
      }
      console.warn(`[IPC] Blocked opening non-http URL: ${url}`);
      return false;
    } catch {
      console.error(`[IPC] Invalid URL for open-external: ${url}`);
      return false;
    }
  });

  // Minimize Window
  ipcMain.handle('minimize-window', () => {
    const win = windowManager.getMainWindow();
    if (isSafeWindow(win)) {
      win.minimize();
    }
    return true;
  });

  // Maximize / Restore Window
  ipcMain.handle('maximize-window', (_event, type?: 'dashboard' | 'game') => {
    const win = type === 'game' ? windowManager.getGameWindow() : windowManager.getMainWindow();
    if (isSafeWindow(win)) {
      if (win.isMaximized()) {
        win.unmaximize();
      } else {
        win.maximize();
      }
    }
    return true;
  });

  // Check if window is maximized
  ipcMain.handle('is-window-maximized', () => {
    const win = windowManager.getMainWindow();
    return isSafeWindow(win) ? win.isMaximized() : false;
  });

  // Check resource status (lib/models)
  ipcMain.handle('check-resource-status', async () => {
    return backendManager.getResourceStatus();
  });

  // Wait for backend to be ready (port 8765 bound)
  ipcMain.handle('wait-for-backend', async (_event, timeoutMs?: number) => {
    const isReady = await backendManager.waitForReady(timeoutMs);
    if (!isReady) throw new Error('Backend failed to start');
    return await backendManager.getBackendConfig();
  });

  // Sync locale across windows
  ipcMain.handle('update-locale', (_event, locale: string) => {
    BrowserWindow.getAllWindows().forEach((win) => {
      safeSend(win, 'locale-changed', locale);
    });
    return true;
  });

  // Set window bounds (x, y, width, height)
  ipcMain.handle('set-window-bounds', (_event, bounds: Partial<Electron.Rectangle>) => {
    const win = BrowserWindow.fromWebContents(_event.sender);
    if (isSafeWindow(win)) {
      win.setBounds(bounds);
    }
    return true;
  });
}
