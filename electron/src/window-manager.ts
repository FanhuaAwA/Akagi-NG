import { join } from 'node:path';

import { app, BrowserWindow, Menu, nativeImage, nativeTheme, screen, Tray } from 'electron';

import type { BackendManager } from './backend-manager.js';
import {
  DASHBOARD_WINDOW_HEIGHT,
  DASHBOARD_WINDOW_MIN_HEIGHT,
  DASHBOARD_WINDOW_MIN_WIDTH,
  DASHBOARD_WINDOW_WIDTH,
  DEV_HUD_URL,
  DEV_SERVER_URL,
  GAME_WINDOW_HEIGHT,
  GAME_WINDOW_WIDTH,
  HUD_MAX_HEIGHT,
  HUD_MAX_WIDTH,
  HUD_MIN_HEIGHT,
  HUD_MIN_WIDTH,
  HUD_WINDOW_HEIGHT,
  HUD_WINDOW_WIDTH,
} from './constants.js';
import {
  DEFAULT_DESKTOP_CONFIG,
  type DesktopConfig,
  getDashboardWindowPolicy,
  getHudMouseInteractionPolicy,
} from './desktop-config.js';
import { GameHandler } from './game-handler.js';
import { createLogger } from './logger.js';
import { buildProxyRules } from './proxy-endpoint.js';
import { isAllowedGameUrl, isTrustedRendererUrl, type RendererRole } from './security-policy.js';
import { getAssetPath, isSafeWindow, safeSend } from './utils.js';

const logger = createLogger('WindowManager');

export class WindowManager {
  private tray: Tray | null = null;
  private dashboardWindow: BrowserWindow | null = null;
  private gameWindow: BrowserWindow | null = null;
  private hudWindow: BrowserWindow | null = null;
  private gameHandler: GameHandler | null = null;
  private desktopConfig: DesktopConfig = DEFAULT_DESKTOP_CONFIG;
  private onCheckForUpdates: (() => void) | undefined;
  private hudRequestedVisible = false;
  private hudClickThroughEnabled = false;
  private hudControlsInteractive = false;
  private lastHudPosition: { x: number; y: number } | null = null;
  private isQuitting: boolean = false;

  constructor(private backendManager: BackendManager) {}

  public setQuitting(quitting: boolean) {
    this.isQuitting = quitting;
  }

  public getMainWindow(): BrowserWindow | null {
    return this.dashboardWindow;
  }

  public getHudWindow(): BrowserWindow | null {
    return this.hudWindow;
  }

  public getRendererRole(contents: Electron.WebContents): RendererRole | null {
    if (this.dashboardWindow?.webContents === contents) return 'dashboard';
    if (this.hudWindow?.webContents === contents) return 'hud';
    return null;
  }

  private hardenTrustedRenderer(win: BrowserWindow, role: RendererRole): void {
    const contents = win.webContents;
    contents.setWindowOpenHandler(() => ({ action: 'deny' }));
    contents.on('will-attach-webview', (event) => event.preventDefault());
    contents.on('will-frame-navigate', (event) => {
      if (!event.isMainFrame || !isTrustedRendererUrl(event.url, role, app.isPackaged)) {
        logger.warn(`Blocked ${role} navigation to ${event.url}`);
        event.preventDefault();
      }
    });
    contents.session.setPermissionCheckHandler(() => false);
    contents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
      callback(false);
    });
  }

  public setupTray(onCheckForUpdates?: () => void): void {
    this.onCheckForUpdates = onCheckForUpdates;
    if (!this.desktopConfig.trayVisible) {
      this.destroyTray();
      return;
    }
    if (this.tray) return;

    const isWin = process.platform === 'win32';
    const isMac = process.platform === 'darwin';

    const iconFile = isWin ? 'torii.ico' : isMac ? 'toriiTemplate.png' : 'torii.png';
    const iconPath = getAssetPath('assets', iconFile);
    const icon = nativeImage.createFromPath(iconPath);

    if (isMac) {
      icon.setTemplateImage(true);
    }
    this.tray = new Tray(icon);

    const contextMenu = Menu.buildFromTemplate([
      {
        label: 'Akagi-NG',
        enabled: false,
      },
      { type: 'separator' },
      {
        label: 'Show Dashboard',
        click: () => {
          this.createDashboardWindow();
        },
      },
      {
        label: 'Check for Updates',
        click: () => {
          if (onCheckForUpdates) {
            onCheckForUpdates();
          }
        },
      },
      { type: 'separator' },
      {
        label: 'Quit Akagi-NG',
        click: () => {
          this.createDashboardWindow();
          const win = this.getMainWindow();
          if (win) {
            win.show();
            win.focus();
            safeSend(win, 'request-app-quit');
          }
        },
      },
    ]);

    this.tray.setToolTip('Akagi-NG');
    this.tray.setContextMenu(contextMenu);

    this.tray.on('click', () => {
      const window = this.getMainWindow();
      if (window) {
        if (window.isMinimized()) {
          window.restore();
          window.show();
        } else if (window.isVisible()) {
          window.hide();
        } else {
          window.show();
        }
        window.focus();
      } else {
        this.createDashboardWindow();
      }
    });
  }

  public async reconcileDesktopSettings(onCheckForUpdates?: () => void): Promise<void> {
    if (onCheckForUpdates) this.onCheckForUpdates = onCheckForUpdates;
    this.desktopConfig = await this.backendManager.getDesktopConfig();

    if (this.desktopConfig.trayVisible) {
      this.setupTray(this.onCheckForUpdates);
    } else {
      this.destroyTray();
    }

    this.applyWindowProtection();
    this.applyHudMouseInteraction();

    if (this.hudRequestedVisible) {
      await this.toggleHudWindow(true);
    }
  }

  public showDashboard(): void {
    if (this.dashboardWindow) {
      if (this.dashboardWindow.isMinimized()) this.dashboardWindow.restore();
      this.dashboardWindow.setSkipTaskbar(false);
      this.dashboardWindow.show();
      this.dashboardWindow.focus();
      return;
    }
    void this.createDashboardWindow();
  }

  public getGameWindow(): BrowserWindow | null {
    return this.gameWindow;
  }

  public async createDashboardWindow(): Promise<void> {
    if (this.dashboardWindow) {
      this.dashboardWindow.show();
      this.dashboardWindow.focus();
      return;
    }

    const dashboardPolicy = getDashboardWindowPolicy(this.desktopConfig);
    this.dashboardWindow = new BrowserWindow({
      width: DASHBOARD_WINDOW_WIDTH,
      height: DASHBOARD_WINDOW_HEIGHT,
      minWidth: DASHBOARD_WINDOW_MIN_WIDTH,
      minHeight: DASHBOARD_WINDOW_MIN_HEIGHT,
      frame: false,
      skipTaskbar: dashboardPolicy.skipTaskbar,
      autoHideMenuBar: true,
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#18181b' : '#ffffff',
      show: false,
      webPreferences: {
        preload: join(__dirname, 'preload.js'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        webviewTag: false,
        partition: 'persist:akagi-ui',
      },
    });
    this.hardenTrustedRenderer(this.dashboardWindow, 'dashboard');
    this.dashboardWindow.setContentProtection(dashboardPolicy.contentProtection);

    this.dashboardWindow.once('ready-to-show', () => {
      if (dashboardPolicy.startVisible) this.dashboardWindow?.show();
    });

    const isDev = !app.isPackaged;

    if (isDev) {
      await this.dashboardWindow.loadURL(DEV_SERVER_URL).catch((err) => {
        logger.error(`Failed to load Dashboard in dev mode: ${err.message}`);
      });
    } else {
      const indexPath = join(__dirname, '../renderer/index.html');
      await this.dashboardWindow.loadFile(indexPath).catch((err) => {
        logger.error(`Failed to load Dashboard: ${err.message}`);
      });
    }

    this.dashboardWindow.on('close', (event) => {
      if (!this.isQuitting) {
        event.preventDefault();
        const closeAction = getDashboardWindowPolicy(this.desktopConfig).closeAction;
        if (closeAction === 'hide') {
          this.dashboardWindow?.hide();
        } else {
          this.dashboardWindow?.minimize();
        }
      }
    });

    this.dashboardWindow.on('closed', () => {
      this.dashboardWindow = null;
    });

    this.dashboardWindow.on('maximize', () => {
      safeSend(this.dashboardWindow, 'window-state-changed', true);
    });

    this.dashboardWindow.on('unmaximize', () => {
      safeSend(this.dashboardWindow, 'window-state-changed', false);
    });
  }

  public async toggleHudWindow(show: boolean): Promise<void> {
    this.hudRequestedVisible = show;
    if (!show) {
      if (this.hudWindow?.isVisible()) this.hudWindow.hide();
      safeSend(this.dashboardWindow, 'hud-visibility-changed', false);
      return;
    }

    this.desktopConfig = await this.backendManager.getDesktopConfig();
    if (!this.hudWindow) {
      await this.createHudWindow();
    }

    if (show) {
      if (this.hudWindow) {
        if (!this.hudWindow.isVisible()) {
          // Mask initial layout shift
          this.hudWindow.setOpacity(0);
          this.hudWindow.show();
          // Short delay to allow renderer to stabilize
          setTimeout(() => {
            if (isSafeWindow(this.hudWindow)) {
              this.hudWindow.setOpacity(1);
            }
          }, 50);
        }
        this.applyHudMouseInteraction();
        this.hudWindow.focus();
        safeSend(this.dashboardWindow, 'hud-visibility-changed', true);
      }
    }
  }

  private async createHudWindow(): Promise<void> {
    if (this.hudWindow) return; // Already exists

    const { width } = screen.getPrimaryDisplay().workAreaSize;

    // Use saved position or default
    const x = this.lastHudPosition?.x ?? width - (HUD_WINDOW_WIDTH + 20);
    const y = this.lastHudPosition?.y ?? 100;

    this.hudWindow = new BrowserWindow({
      x,
      y,
      width: HUD_WINDOW_WIDTH,
      height: HUD_WINDOW_HEIGHT,
      minWidth: HUD_MIN_WIDTH,
      minHeight: HUD_MIN_HEIGHT,
      maxWidth: HUD_MAX_WIDTH,
      maxHeight: HUD_MAX_HEIGHT,
      frame: false,
      transparent: true,
      backgroundColor: '#00000000',
      show: false, // Keep hidden initially
      hasShadow: false,
      resizable: true,
      skipTaskbar: true,
      webPreferences: {
        preload: join(__dirname, 'preload.js'),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        webviewTag: false,
        partition: 'persist:akagi-ui',
      },
    });
    this.hardenTrustedRenderer(this.hudWindow, 'hud');

    // Enforce high-level always-on-top (works over fullscreen apps/games on most OS)
    this.hudWindow.setAlwaysOnTop(true, 'screen-saver');
    this.hudWindow.setContentProtection(this.desktopConfig.captureProtection);
    this.applyHudMouseInteraction();

    // Ensure visibility across virtual desktops (Mac/Win)
    this.hudWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

    // Enforce 16:9 aspect ratio natively
    this.hudWindow.setAspectRatio(16 / 9);

    // Save position whenever user moves it
    this.hudWindow.on('moved', () => {
      if (isSafeWindow(this.hudWindow)) {
        const [x, y] = this.hudWindow.getPosition();
        this.lastHudPosition = { x, y };
      }
    });

    const isDev = !app.isPackaged;
    const loadPromise = isDev
      ? this.hudWindow.loadURL(DEV_HUD_URL)
      : this.hudWindow.loadFile(join(__dirname, '../renderer/index.html'), { hash: '/hud' });

    await loadPromise.catch((err) => logger.error('Failed to load HUD:', err));

    this.hudWindow.on('close', (e) => {
      // If the app is quitting, allow close. Otherwise, just hide.
      if (!this.isQuitting) {
        e.preventDefault();
        this.hudWindow?.hide();
        safeSend(this.dashboardWindow, 'hud-visibility-changed', false);
      }
    });

    this.hudWindow.on('closed', () => {
      this.hudWindow = null;
      this.hudControlsInteractive = false;
    });
  }

  public getHudClickThroughStatus() {
    return getHudMouseInteractionPolicy({
      clickThroughEnabled: this.hudClickThroughEnabled,
      controlsInteractive: this.hudControlsInteractive,
    });
  }

  public setHudClickThrough(enabled: boolean) {
    this.hudClickThroughEnabled = enabled;
    this.applyHudMouseInteraction();
    return this.getHudClickThroughStatus();
  }

  public setHudControlsInteractive(interactive: boolean) {
    this.hudControlsInteractive = interactive;
    this.applyHudMouseInteraction();
    return this.getHudClickThroughStatus();
  }

  public async shutdown(): Promise<void> {
    this.destroyTray();
  }

  public resetGameWindowForProxyChange(): boolean {
    if (!isSafeWindow(this.gameWindow)) {
      this.gameWindow = null;
      return false;
    }

    logger.info('Closing the active game window because its proxy route changed.');
    if (this.gameHandler) {
      this.gameHandler.detach();
      this.gameHandler = null;
    }
    const currentWindow = this.gameWindow;
    this.gameWindow = null;
    currentWindow.destroy();
    return true;
  }

  private destroyTray(): void {
    if (!this.tray) return;
    this.tray.destroy();
    this.tray = null;
  }

  private applyWindowProtection(): void {
    if (isSafeWindow(this.dashboardWindow)) {
      this.dashboardWindow.setSkipTaskbar(false);
      this.dashboardWindow.setContentProtection(this.desktopConfig.captureProtection);
    }
    if (isSafeWindow(this.hudWindow)) {
      this.hudWindow.setSkipTaskbar(true);
      this.hudWindow.setContentProtection(this.desktopConfig.captureProtection);
    }
  }

  private applyHudMouseInteraction(): void {
    const status = this.getHudClickThroughStatus();
    if (isSafeWindow(this.hudWindow)) {
      this.hudWindow.setIgnoreMouseEvents(status.ignoreMouseEvents, { forward: true });
      safeSend(this.hudWindow, 'hud-click-through-changed', status);
    }
  }

  public async createGameWindow(options: {
    url?: string;
    useMitm?: boolean;
    platform?: string;
  }): Promise<void> {
    const { url, useMitm, platform = 'auto' } = options;

    if (isSafeWindow(this.gameWindow)) {
      this.gameWindow.focus();
      return;
    }
    this.gameWindow = null; // Clean up zombie reference and proceed with creation

    if (!url) {
      logger.warn('No URL provided for Game Window!');
      return;
    }
    if (!isAllowedGameUrl(url, platform, url)) {
      throw new Error('Blocked untrusted game URL.');
    }

    this.gameWindow = new BrowserWindow({
      width: GAME_WINDOW_WIDTH,
      height: GAME_WINDOW_HEIGHT,
      useContentSize: true,
      maximizable: true,
      autoHideMenuBar: true,
      backgroundColor: nativeTheme.shouldUseDarkColors ? '#18181b' : '#ffffff',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        webviewTag: false,
      },
    });

    this.gameWindow.webContents.setWindowOpenHandler(({ url: targetUrl }) => {
      if (!isAllowedGameUrl(targetUrl, platform, url)) {
        logger.warn(`Blocked untrusted game popup: ${targetUrl}`);
        return { action: 'deny' };
      }
      return {
        action: 'allow',
        overrideBrowserWindowOptions: {
          autoHideMenuBar: true,
          backgroundColor: nativeTheme.shouldUseDarkColors ? '#18181b' : '#ffffff',
          webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            sandbox: true,
            webSecurity: true,
            allowRunningInsecureContent: false,
            webviewTag: false,
          },
        },
      };
    });
    this.gameWindow.webContents.on('will-frame-navigate', (event) => {
      if (event.isMainFrame && !isAllowedGameUrl(event.url, platform, url)) {
        logger.warn(`Blocked untrusted game navigation: ${event.url}`);
        event.preventDefault();
      }
    });

    // Handle F11 for fullscreen toggle
    this.gameWindow.webContents.on('before-input-event', (event, input) => {
      if (input.type === 'keyDown' && input.key === 'F11') {
        const isFullScreen = this.gameWindow?.isFullScreen();
        this.gameWindow?.setFullScreen(!isFullScreen);
        event.preventDefault();
      }
    });

    // Frontend should always provide a valid URL.
    // Sanitize User Agent to remove Electron fingerprint
    const defaultUA = this.gameWindow.webContents.session.getUserAgent();
    // Remove "akagi-ng-desktop/1.0.0" and "Electron/x.y.z"
    const cleanUA = defaultUA
      .replace(/akagi-ng-desktop\/\S+\s/g, '')
      .replace(/Electron\/\S+\s/g, '');

    this.gameWindow.webContents.setUserAgent(cleanUA);

    const gameSession = this.gameWindow.webContents.session;

    try {
      // Set up proxy if using MITM
      if (useMitm) {
        const mitm = await this.backendManager.getMitmConfig();
        const proxyRules = buildProxyRules(mitm.host, mitm.port);
        logger.info(`Routing game traffic through MITM proxy: ${proxyRules}`);
        await gameSession.setProxy({
          mode: 'fixed_servers',
          proxyRules,
          proxyBypassRules: '127.0.0.1,localhost',
        });
      } else {
        // Electron sessions persist proxy settings. Explicitly clear a previous MITM route.
        await gameSession.setProxy({ mode: 'direct' });
      }

      // Existing pooled sockets keep their old route after setProxy until explicitly closed.
      await gameSession.closeAllConnections();
    } catch (error) {
      logger.error('Failed to initialize game proxy:', error);
      // A partially applied fixed proxy belongs to the shared Electron session.
      // Restore direct mode before discarding the blank window so a corrected
      // configuration can be retried immediately.
      await gameSession.setProxy({ mode: 'direct' }).catch((resetError: unknown) => {
        logger.error('Failed to reset game proxy after initialization error:', resetError);
      });
      await gameSession.closeAllConnections().catch(() => {});
      if (isSafeWindow(this.gameWindow)) this.gameWindow.destroy();
      this.gameWindow = null;
      throw error;
    }

    if (!useMitm) {
      // If NOT using MITM, attach GameHandler (Debugger API) for local interception
      // MUST attach before loading URL to capture early WebSocket traffic and avoid crash
      try {
        if (isSafeWindow(this.gameWindow) && !this.gameWindow.webContents.isDestroyed()) {
          const backend = await this.backendManager.getBackendConfig();
          const apiBase = `http://${backend.host}:${backend.port}`;
          this.gameHandler = new GameHandler(this.gameWindow.webContents, apiBase);
          this.gameHandler.attach(); // Do not await, let it happen in parallel with loadURL
        }
      } catch (e) {
        logger.error('Failed to attach GameHandler:', e);
      }
    }

    if (isSafeWindow(this.gameWindow)) {
      this.gameWindow.on('closed', () => {
        if (this.gameHandler) {
          this.gameHandler.detach();
          this.gameHandler = null;
        }
        this.gameWindow = null;
      });
    }

    try {
      await this.gameWindow.loadURL(url);
    } catch (err) {
      const error = err as { code?: string; errno?: number; message?: string };
      // ERR_ABORTED can happen during redirects or if the navigation is cancelled by the page logic
      // but it doesn't always mean the load failed.
      if (error.code === 'ERR_ABORTED' || error.errno === -3) {
        logger.warn(`Navigation aborted for ${url}, attempting to proceed...`);
      } else {
        logger.error(`Failed to load game URL: ${error.message ?? 'Unknown Error'}`);
        // Clean up failed window immediately
        if (isSafeWindow(this.gameWindow)) {
          this.gameWindow.close();
        }
        this.gameWindow = null;
        throw err;
      }
    }
  }
}
