import { app, ipcMain, shell } from 'electron';

import type { BackendManager } from './backend-manager.js';
import { EXIT_ANIMATION_DELAY_MS } from './constants.js';
import { createLogger } from './logger.js';
import type { MihomoManager } from './mihomo-manager.js';
import {
  IPC_CHANNEL_ROLES,
  type IpcChannel,
  isRoleAllowed,
  isSafeExternalUrl,
  isTrustedRendererUrl,
} from './security-policy.js';
import { isSafeWindow, safeSend } from './utils.js';
import type { WindowManager } from './window-manager.js';

const logger = createLogger('IPC');
const SUPPORTED_LOCALES = new Set(['zh-CN', 'zh-TW', 'en-US', 'ja-JP']);
const SUPPORTED_GAME_PLATFORMS = new Set(['auto', 'majsoul', 'tenhou']);
const MAX_BACKEND_WAIT_MS = 120_000;

function requireBoolean(value: unknown, name: string): boolean {
  if (typeof value !== 'boolean') throw new TypeError(`${name} must be a boolean.`);
  return value;
}

function requireLocale(value: unknown): string {
  if (typeof value !== 'string' || !SUPPORTED_LOCALES.has(value)) {
    throw new TypeError('Unsupported locale.');
  }
  return value;
}

function parseStartGameOptions(value: unknown): {
  url?: string;
  useMitm?: boolean;
  platform?: string;
} {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Game options must be an object.');
  }
  const input = value as Record<string, unknown>;
  if (typeof input.url !== 'string' || !input.url.trim()) {
    throw new TypeError('Game URL must be a non-empty string.');
  }
  if (input.useMitm !== undefined && typeof input.useMitm !== 'boolean') {
    throw new TypeError('useMitm must be a boolean.');
  }
  if (typeof input.platform !== 'string' || !SUPPORTED_GAME_PLATFORMS.has(input.platform)) {
    throw new TypeError('Unsupported game platform.');
  }
  return {
    url: input.url,
    useMitm: input.useMitm,
    platform: input.platform,
  } as { url?: string; useMitm?: boolean; platform?: string };
}

export function registerIpcHandlers(
  windowManager: WindowManager,
  backendManager: BackendManager,
  mihomoManager: MihomoManager,
  shutdownApplication: () => Promise<void>,
) {
  const assertTrustedRenderer = (event: Electron.IpcMainInvokeEvent, channel: IpcChannel) => {
    const role = windowManager.getRendererRole(event.sender);
    const frame = event.senderFrame;
    if (
      !role ||
      !frame ||
      frame !== event.sender.mainFrame ||
      !isRoleAllowed(channel, role) ||
      !isTrustedRendererUrl(frame.url, role, app.isPackaged)
    ) {
      logger.warn(`Blocked ${channel} IPC request from an untrusted renderer.`);
      throw new Error('Unauthorized desktop request.');
    }
  };

  const handle = <TArgs extends readonly unknown[], TResult>(
    channel: IpcChannel,
    listener: (event: Electron.IpcMainInvokeEvent, ...args: TArgs) => TResult | Promise<TResult>,
  ) => {
    if (!IPC_CHANNEL_ROLES[channel]) throw new Error(`Missing IPC policy for ${channel}.`);
    ipcMain.handle(channel, async (event, ...args: unknown[]) => {
      assertTrustedRenderer(event, channel);
      return listener(event, ...(args as unknown as TArgs));
    });
  };

  handle('toggle-hud', async (_event, show: unknown) => {
    await windowManager.toggleHudWindow(requireBoolean(show, 'show'));
    return windowManager.getHudClickThroughStatus();
  });

  handle('desktop-reconcile', async () => {
    await windowManager.reconcileDesktopSettings();
    return windowManager.getHudClickThroughStatus();
  });

  handle('hud-click-through-status', () => windowManager.getHudClickThroughStatus());
  handle('hud-set-click-through', (_event, enabled: unknown) =>
    windowManager.setHudClickThrough(requireBoolean(enabled, 'enabled')),
  );
  handle('hud-set-controls-interactive', (_event, interactive: unknown) =>
    windowManager.setHudControlsInteractive(requireBoolean(interactive, 'interactive')),
  );

  handle('start-game', async (_event, options: unknown) => {
    await windowManager.createGameWindow(parseStartGameOptions(options));
    return true;
  });

  let rendererShutdownPromise: Promise<boolean> | null = null;
  handle('request-shutdown', () => {
    if (rendererShutdownPromise) return rendererShutdownPromise;
    rendererShutdownPromise = (async () => {
      safeSend(windowManager.getMainWindow(), 'exit-animation-start');
      const animation = new Promise((resolve) => setTimeout(resolve, EXIT_ANIMATION_DELAY_MS));
      await Promise.all([animation, shutdownApplication()]);
      app.quit();
      return true;
    })();
    return rendererShutdownPromise;
  });

  handle('mihomo-status', () => mihomoManager.getStatus());
  handle('mihomo-reconcile', async (_event, gameProxyChanged: unknown = false) => {
    const shouldResetGame = requireBoolean(gameProxyChanged, 'gameProxyChanged');
    const gameWindowClosed = shouldResetGame
      ? windowManager.resetGameWindowForProxyChange()
      : false;
    const status = await mihomoManager.reconcile();
    return { ...status, gameWindowClosed };
  });
  handle('mihomo-stop', async () => {
    await mihomoManager.stop();
    return mihomoManager.getStatus();
  });

  handle('update-liqi', async () => {
    try {
      logger.info('Starting manual update of liqi.json from official servers...');
      const verRes = await fetch('https://game.maj-soul.com/1/version.json');
      if (!verRes.ok) throw new Error(`Failed to fetch version.json: ${verRes.status}`);
      const verData = (await verRes.json()) as { version: string };

      const resMapUrl = `https://game.maj-soul.com/1/resversion${verData.version}.json`;
      const mapRes = await fetch(resMapUrl);
      if (!mapRes.ok) throw new Error(`Failed to fetch res mapping: ${mapRes.status}`);
      const mapData = (await mapRes.json()) as { res: Record<string, { prefix: string }> };
      const prefix = mapData.res['res/proto/liqi.json']?.prefix;
      if (!prefix) throw new Error('Could not find res/proto/liqi.json in mapping.');

      const liqiRes = await fetch(`https://game.maj-soul.com/1/${prefix}/res/proto/liqi.json`);
      if (!liqiRes.ok) throw new Error(`Failed to fetch liqi.json: ${liqiRes.status}`);
      const liqiText = await liqiRes.text();

      const config = await backendManager.getBackendConfig();
      const backendRes = await fetch(`http://127.0.0.1:${config.port}/api/protocol/update`, {
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

  handle('open-external', async (_event, value: unknown) => {
    if (typeof value !== 'string' || !isSafeExternalUrl(value)) {
      logger.warn('Blocked unsafe external URL.');
      return false;
    }
    await shell.openExternal(new URL(value).toString());
    return true;
  });

  handle('minimize-window', () => {
    const win = windowManager.getMainWindow();
    if (isSafeWindow(win)) win.minimize();
    return true;
  });

  handle('close-window', () => {
    const win = windowManager.getMainWindow();
    if (isSafeWindow(win)) win.close();
    return true;
  });

  handle('maximize-window', () => {
    const win = windowManager.getMainWindow();
    if (isSafeWindow(win)) {
      if (win.isMaximized()) win.unmaximize();
      else win.maximize();
    }
    return true;
  });

  handle('is-window-maximized', () => {
    const win = windowManager.getMainWindow();
    return isSafeWindow(win) ? win.isMaximized() : false;
  });

  handle('check-resource-status', () => backendManager.getResourceStatus());

  handle('get-startup-config', () => backendManager.getStartupConfig());

  handle('wait-for-backend', async (_event, value?: unknown) => {
    const timeoutMs = value === undefined ? undefined : Number(value);
    if (
      timeoutMs !== undefined &&
      (!Number.isFinite(timeoutMs) || timeoutMs < 0 || timeoutMs > MAX_BACKEND_WAIT_MS)
    ) {
      throw new TypeError('Invalid backend wait timeout.');
    }
    const isReady = await backendManager.waitForReady(timeoutMs);
    if (!isReady) throw new Error('Backend failed to start');
    return backendManager.getBackendConfig();
  });

  handle('update-locale', (_event, value: unknown) => {
    const locale = requireLocale(value);
    safeSend(windowManager.getMainWindow(), 'locale-changed', locale);
    safeSend(windowManager.getHudWindow(), 'locale-changed', locale);
    return true;
  });
}
