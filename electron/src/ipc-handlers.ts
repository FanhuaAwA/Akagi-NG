import { app, BrowserWindow, ipcMain, shell } from 'electron';

import type { BackendManager } from './backend-manager.js';
import { EXIT_ANIMATION_DELAY_MS } from './constants.js';
import { createLogger } from './logger.js';
import type { MihomoManager } from './mihomo-manager.js';
import { isSafeWindow, safeSend } from './utils.js';
import type { WindowManager } from './window-manager.js';

const logger = createLogger('IPC');

export function registerIpcHandlers(
  windowManager: WindowManager,
  backendManager: BackendManager,
  mihomoManager: MihomoManager,
) {
  const assertTrustedDashboard = (event: Electron.IpcMainInvokeEvent) => {
    const dashboard = windowManager.getMainWindow();
    if (
      !isSafeWindow(dashboard) ||
      event.sender !== dashboard.webContents ||
      event.senderFrame !== event.sender.mainFrame
    ) {
      logger.warn('Blocked privileged IPC request from an untrusted renderer.');
      throw new Error('Unauthorized desktop request.');
    }
  };

  // Toggle HUD Window
  ipcMain.handle('toggle-hud', async (_event, show: boolean) => {
    await windowManager.toggleHudWindow(show);
    return windowManager.getHudClickThroughStatus();
  });

  ipcMain.handle('desktop-reconcile', async () => {
    await windowManager.reconcileDesktopSettings();
    return windowManager.getHudClickThroughStatus();
  });

  ipcMain.handle('hud-click-through-status', () => windowManager.getHudClickThroughStatus());
  ipcMain.handle('hud-set-click-through', (_event, enabled: boolean) =>
    windowManager.setHudClickThrough(enabled),
  );
  ipcMain.handle('hud-set-controls-interactive', (_event, interactive: boolean) =>
    windowManager.setHudControlsInteractive(interactive),
  );

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

    const animation = new Promise((resolve) => setTimeout(resolve, EXIT_ANIMATION_DELAY_MS));
    await mihomoManager.stop().catch((err: unknown) => logger.error('mihomo stop error:', err));
    await backendManager.stop().catch((err: unknown) => logger.error('Backend stop error:', err));
    await Promise.all([
      animation,
      windowManager.shutdown().catch((err: unknown) => logger.error('Window stop error:', err)),
    ]);

    app.quit();
    return true;
  });

  ipcMain.handle('mihomo-status', async (event) => {
    assertTrustedDashboard(event);
    return mihomoManager.getStatus();
  });
  ipcMain.handle('mihomo-reconcile', async (event) => {
    assertTrustedDashboard(event);
    return mihomoManager.reconcile();
  });
  ipcMain.handle('mihomo-stop', async (event) => {
    assertTrustedDashboard(event);
    await mihomoManager.stop();
    return mihomoManager.getStatus();
  });

  // Manual Protocol Update
  ipcMain.handle('update-liqi', async () => {
    try {
      logger.info('Starting manual update of liqi.json from official servers...');
      // Step 1: Get global version
      const verRes = await fetch('https://game.maj-soul.com/1/version.json');
      if (!verRes.ok) throw new Error(`Failed to fetch version.json: ${verRes.status}`);
      const verData = (await verRes.json()) as { version: string };

      // Step 2: Get res mapping
      const resMapUrl = `https://game.maj-soul.com/1/resversion${verData.version}.json`;
      const mapRes = await fetch(resMapUrl);
      if (!mapRes.ok) throw new Error(`Failed to fetch res mapping: ${mapRes.status}`);
      const mapData = (await mapRes.json()) as {
        res: Record<string, { prefix: string }>;
      };

      const prefix = mapData.res['res/proto/liqi.json']?.prefix;
      if (!prefix) throw new Error('Could not find res/proto/liqi.json in mapping.');

      // Step 3: Fetch liqi.json
      const liqiUrl = `https://game.maj-soul.com/1/${prefix}/res/proto/liqi.json`;
      const liqiRes = await fetch(liqiUrl);
      if (!liqiRes.ok) throw new Error(`Failed to fetch liqi.json: ${liqiRes.status}`);
      const liqiText = await liqiRes.text();

      // Send to backend via dedicated protocol update endpoint
      const config = await backendManager.getBackendConfig();
      const port = config.port;

      const backendRes = await fetch(`http://127.0.0.1:${port}/api/protocol/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: liqiText }),
      });

      if (!backendRes.ok) {
        const body = (await backendRes.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error ?? `Backend returned ${backendRes.status}`);
      }

      logger.info('Successfully updated liqi.json via backend.');
      return true;
    } catch (err) {
      logger.error('Failed to update liqi.json:', err instanceof Error ? err.message : String(err));
      throw err;
    }
  });

  // Open external URL in system browser
  ipcMain.handle('open-external', async (_event, url: string) => {
    try {
      const parsed = new URL(url);
      if (['http:', 'https:'].includes(parsed.protocol)) {
        await shell.openExternal(url);
        return true;
      }
      logger.warn(`Blocked opening non-http URL: ${url}`);
      return false;
    } catch {
      logger.error(`Invalid URL for open-external: ${url}`);
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

  // Close Window (Minimize to Tray)
  ipcMain.handle('close-window', (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (isSafeWindow(win)) {
      win.close();
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
