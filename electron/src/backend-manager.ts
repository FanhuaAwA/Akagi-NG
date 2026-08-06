import type { ChildProcess } from 'node:child_process';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { delimiter, join } from 'node:path';

import { app, dialog } from 'electron';

import { DEFAULT_DESKTOP_CONFIG, type DesktopConfig } from './desktop-config.js';
import { createLogger } from './logger.js';

interface AppSettings {
  server?: {
    host?: string;
    port?: number;
  };
  mitm?: {
    enabled?: boolean;
    host?: string;
    port?: number;
  };
  mihomo?: {
    enabled?: boolean;
    mixed_port?: number;
    controller_port?: number;
    strict_route?: boolean;
  };
  desktop?: {
    capture_protection?: boolean;
    tray_visible?: boolean;
  };
}

interface PluginState {
  mitm_required?: boolean;
}

export interface StartupConfig {
  host: string;
  port: number;
  settings: Record<string, unknown>;
}

const BLOCKED_SETTINGS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

function isSettingsObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Fill fields added by newer releases without discarding any existing user
 * values. The Python backend performs the durable, semantic migration later;
 * this merge only guarantees that the renderer's first frame has a complete
 * shape while the backend is still starting.
 */
export function mergeStartupSettings(
  defaults: Record<string, unknown>,
  current: Record<string, unknown>,
): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const [key, defaultValue] of Object.entries(defaults)) {
    if (BLOCKED_SETTINGS_KEYS.has(key)) continue;
    merged[key] = isSettingsObject(defaultValue)
      ? mergeStartupSettings(defaultValue, {})
      : structuredClone(defaultValue);
  }
  for (const [key, currentValue] of Object.entries(current)) {
    if (BLOCKED_SETTINGS_KEYS.has(key)) continue;
    const defaultValue = defaults[key];
    merged[key] =
      isSettingsObject(defaultValue) && isSettingsObject(currentValue)
        ? mergeStartupSettings(defaultValue, currentValue)
        : structuredClone(currentValue);
  }
  return merged;
}

import {
  BACKEND_READY_TIMEOUT_MS,
  BACKEND_SHUTDOWN_API_TIMEOUT_MS,
  BACKEND_SHUTDOWN_TIMEOUT_MS,
  BACKEND_STARTUP_CHECK_INTERVAL_MS,
  BACKEND_STARTUP_CHECK_MAX_INTERVAL_MS,
  BACKEND_STARTUP_CHECK_RETRIES,
  BACKEND_STARTUP_CHECK_TIMEOUT_MS,
} from './constants.js';
import type { ResourceStatus } from './resource-checker.js';
import { ResourceChecker } from './resource-checker.js';
import { getAssetPath, getProjectRoot, getUserDataRoot } from './utils.js';

const logger = createLogger('BackendManager');

export class BackendManager {
  private pyProcess: ChildProcess | null = null;
  private resourceChecker: ResourceChecker;
  private resourceStatus: ResourceStatus | null = null;
  private resourceStatusPromise: Promise<ResourceStatus> | null = null;
  private isReadyState: boolean = false;
  private readyError: Error | null = null;
  private readyPromise: Promise<void>;
  private resolveReady!: () => void;
  private rejectReady!: (reason?: Error) => void;
  private isMockMode: boolean = false;
  private isClosing: boolean = false;
  private startupStartedAt: number | null = null;

  private async getSettings(): Promise<AppSettings> {
    try {
      const settingsPath = join(getUserDataRoot(), 'config', 'settings.json');
      let fileContent: string;
      try {
        fileContent = await readFile(settingsPath, 'utf8');
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT' || !app.isPackaged) throw error;
        // Python performs the durable first-run migration. This fallback keeps
        // pre-backend desktop settings correct during the same startup race.
        fileContent = await readFile(getAssetPath('assets', 'settings.default.json'), 'utf8');
      }
      return JSON.parse(fileContent) as AppSettings;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
        logger.warn(
          'Failed to read settings.json for config:',
          err instanceof Error ? err.message : String(err),
        );
      }

      // Keep the trusted renderer bootable while Python repairs a malformed or
      // outdated writable settings file. The bundled defaults are immutable and
      // version-matched, so they are a safe startup-only fallback.
      if (app.isPackaged) {
        try {
          return JSON.parse(
            await readFile(getAssetPath('assets', 'settings.default.json'), 'utf8'),
          ) as AppSettings;
        } catch (fallbackError) {
          logger.warn(
            'Failed to read bundled startup settings:',
            fallbackError instanceof Error ? fallbackError.message : String(fallbackError),
          );
        }
      }
    }
    return {};
  }

  private async getPluginMitmRequired(): Promise<boolean> {
    try {
      const statePath = join(getUserDataRoot(), 'config', 'plugins.json');
      let fileContent: string;
      try {
        fileContent = await readFile(statePath, 'utf8');
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT' || !app.isPackaged) throw error;
        fileContent = await readFile(getAssetPath('config', 'plugins.json'), 'utf8');
      }
      const state = JSON.parse(fileContent) as PluginState;
      return state.mitm_required === true;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
        logger.warn(
          'Failed to read plugins.json for effective MITM state:',
          err instanceof Error ? err.message : String(err),
        );
      }
      return false;
    }
  }

  public async getBackendConfig(): Promise<{ host: string; port: number }> {
    if (process.argv.includes('--mock')) {
      return { host: '127.0.0.1', port: 8765 };
    }
    const settings = await this.getSettings();
    return {
      host: settings.server?.host ?? '127.0.0.1',
      port: settings.server?.port ?? 8765,
    };
  }

  public async getStartupConfig(): Promise<StartupConfig> {
    const settings = await this.getSettings();
    let startupSettings = settings as Record<string, unknown>;
    try {
      const bundledDefaults = JSON.parse(
        await readFile(getAssetPath('assets', 'settings.default.json'), 'utf8'),
      ) as Record<string, unknown>;
      startupSettings = mergeStartupSettings(bundledDefaults, startupSettings);
    } catch (error) {
      logger.warn(
        'Failed to normalize startup settings with bundled defaults:',
        error instanceof Error ? error.message : String(error),
      );
    }
    const publicSettings = structuredClone(startupSettings);
    const ot = publicSettings.ot;
    if (ot && typeof ot === 'object' && !Array.isArray(ot)) {
      const publicOt = ot as Record<string, unknown>;
      const legacyFlyAKey = typeof publicOt.flya_api_key === 'string' ? publicOt.flya_api_key : '';
      if (legacyFlyAKey) {
        publicOt.flya_api_key_configured = true;
        publicOt.flya_api_key_last4 = legacyFlyAKey.slice(-4);
      }
      publicOt.flya_api_key = '';
    }
    return {
      host: settings.server?.host ?? '127.0.0.1',
      port: settings.server?.port ?? 8765,
      // The settings file contains the same public representation returned by
      // /api/settings. Secrets managed by the credential store are not present.
      settings: publicSettings,
    };
  }

  public async getMitmConfig(): Promise<{ host: string; port: number }> {
    const settings = await this.getSettings();
    return {
      host: settings.mitm?.host ?? '127.0.0.1',
      port: settings.mitm?.port ?? 6789,
    };
  }

  public async getDesktopConfig(): Promise<DesktopConfig> {
    const settings = await this.getSettings();
    const desktop = settings.desktop;
    return {
      captureProtection: desktop?.capture_protection ?? DEFAULT_DESKTOP_CONFIG.captureProtection,
      trayVisible: desktop?.tray_visible ?? DEFAULT_DESKTOP_CONFIG.trayVisible,
    };
  }

  public async getProxyConfig(): Promise<{
    mitm: { enabled: boolean; host: string; port: number };
    mihomo: {
      enabled: boolean;
      mixedPort: number;
      controllerPort: number;
      strictRoute: boolean;
    };
  }> {
    const [settings, pluginMitmRequired] = await Promise.all([
      this.getSettings(),
      this.getPluginMitmRequired(),
    ]);
    return {
      mitm: {
        // The TUN must forward to the single Akagi-NG MITM listener whether it
        // was requested by core settings or by an enabled traffic plugin.
        enabled: (settings.mitm?.enabled ?? false) || pluginMitmRequired,
        host: settings.mitm?.host ?? '127.0.0.1',
        port: settings.mitm?.port ?? 6789,
      },
      mihomo: {
        enabled: settings.mihomo?.enabled ?? false,
        mixedPort: settings.mihomo?.mixed_port ?? 7890,
        controllerPort: settings.mihomo?.controller_port ?? 9090,
        strictRoute: settings.mihomo?.strict_route ?? false,
      },
    };
  }

  public isRunning(): boolean {
    if (this.isMockMode) return true;
    return !!this.pyProcess && !this.pyProcess.killed;
  }

  constructor() {
    this.readyPromise = new Promise((resolve, reject) => {
      this.resolveReady = resolve;
      this.rejectReady = reject;
    });
    this.readyPromise.catch(() => {});

    this.resourceChecker = new ResourceChecker(getProjectRoot());
  }

  public getResourceStatus(): Promise<ResourceStatus> {
    if (this.resourceStatus) return Promise.resolve(this.resourceStatus);
    if (this.resourceStatusPromise) return this.resourceStatusPromise;

    const checkPromise = Promise.resolve()
      .then(() => this.resourceChecker.check())
      .then((status) => {
        this.resourceStatus = status;
        return status;
      });
    this.resourceStatusPromise = checkPromise;
    void checkPromise.then(
      () => {
        if (this.resourceStatusPromise === checkPromise) this.resourceStatusPromise = null;
      },
      () => {
        if (this.resourceStatusPromise === checkPromise) this.resourceStatusPromise = null;
      },
    );
    return checkPromise;
  }

  public async start(): Promise<boolean> {
    if (this.isClosing) {
      logger.info('Backend startup skipped because shutdown has started.');
      return false;
    }

    try {
      this.startupStartedAt = Date.now();
      if (this.pyProcess) {
        logger.info('Backend already running.');
        return true;
      }

      const isDev = !app.isPackaged;

      if (process.argv.includes('--mock')) {
        return this.startMockBackend();
      } else if (isDev) {
        return this.startDevBackend();
      } else {
        return await this.startProdBackend();
      }
    } catch (error) {
      if (this.isClosing) {
        logger.info('Backend startup was cancelled during shutdown.');
        return false;
      }

      const startupError =
        error instanceof Error
          ? error
          : new Error(`Unknown backend startup error: ${String(error)}`);
      logger.error('Backend startup failed:', startupError);
      this.markFailed(startupError);
      dialog.showErrorBox('Backend Initialization Failed', startupError.message);
      return false;
    }
  }

  private startDevBackend(): boolean {
    logger.info('Starting Python backend in DEV mode...');
    if (this.isClosing) return false;

    const projectRoot = getProjectRoot();
    const backendRoot = join(projectRoot, 'akagi_backend');
    const venvDir = join(backendRoot, '.venv');

    let pythonExecutable: string;
    if (process.platform === 'win32') {
      pythonExecutable = join(venvDir, 'Scripts', 'python.exe');
    } else {
      pythonExecutable = join(venvDir, 'bin', 'python');
    }

    if (!existsSync(pythonExecutable)) {
      const errorMsg = `Python executable NOT FOUND at: ${pythonExecutable}. Please check your environment.`;
      logger.error(errorMsg);
      dialog.showErrorBox('Backend Initialization Failed', errorMsg);
      this.markFailed(new Error(errorMsg));
      return false;
    }

    const env = {
      ...process.env,
      PYTHONDONTWRITEBYTECODE: '1',
      PYTHONUNBUFFERED: '1',
      PYTHONPATH: process.env.PYTHONPATH
        ? `${backendRoot}${delimiter}${process.env.PYTHONPATH}`
        : backendRoot,
    };

    if (this.isClosing) return false;
    const childProcess = spawn(pythonExecutable, ['-m', 'akagi_ng'], {
      cwd: projectRoot,
      env: env,
    });
    this.pyProcess = childProcess;

    this.setupListeners();
    if (this.isClosing) {
      this.terminateCancelledProcess(childProcess);
      return false;
    }
    void this.startHealthCheck();
    return true;
  }

  private startMockBackend(): boolean {
    if (this.isClosing) return false;
    logger.info('Starting mock backend service...');
    this.isMockMode = true;
    if (this.isClosing) {
      this.isMockMode = false;
      return false;
    }
    this.markReady();
    return true;
  }

  private async startProdBackend(): Promise<boolean> {
    logger.info('Starting Python backend service...');

    const resourceCheckStartedAt = Date.now();
    const resourceStatus = await this.getResourceStatus();
    if (this.isClosing) {
      logger.info('Backend startup cancelled after resource availability check.');
      return false;
    }
    if (!resourceStatus.lib) {
      const msg = 'Required native libraries are missing. The backend cannot be started.';
      logger.error(msg);
      dialog.showErrorBox('Startup Error', msg);
      this.markFailed(new Error(msg));
      return false;
    }
    logger.info(`Resource availability checked in ${Date.now() - resourceCheckStartedAt} ms.`);

    const isWin = process.platform === 'win32';
    const bundleDir = getAssetPath('bin');
    const pythonExecutable = join(bundleDir, 'python', isWin ? 'akagi-ng.exe' : 'bin/akagi-ng');

    if (!existsSync(pythonExecutable)) {
      const msg = `Portable Python not found at ${pythonExecutable}`;
      logger.error(msg);
      dialog.showErrorBox('Startup Error', msg);
      this.markFailed(new Error(msg));
      return false;
    }

    try {
      if (this.isClosing) return false;
      const childProcess = spawn(pythonExecutable, ['-m', 'akagi_ng'], {
        cwd: getProjectRoot(),
        env: {
          ...process.env,
          AKAGI_USER_DATA_DIR: getUserDataRoot(),
          PYTHONDONTWRITEBYTECODE: '1',
          PYTHONPATH: join(bundleDir, 'app_packages'),
          PYTHONUNBUFFERED: '1',
        },
      });
      this.pyProcess = childProcess;
      logger.info(
        `Backend process spawned after ${this.startupStartedAt ? Date.now() - this.startupStartedAt : 0} ms.`,
      );

      this.setupListeners();
      if (this.isClosing) {
        this.terminateCancelledProcess(childProcess);
        return false;
      }
      void this.startHealthCheck();
      return true;
    } catch (e) {
      const msg = `Backend initialization failed: ${e instanceof Error ? e.message : String(e)}`;
      logger.error(msg);
      dialog.showErrorBox('Startup Error', msg);
      this.markFailed(new Error(msg));
      return false;
    }
  }

  private async startHealthCheck() {
    const deadline = Date.now() + BACKEND_READY_TIMEOUT_MS;
    try {
      for (let i = 0; i < BACKEND_STARTUP_CHECK_RETRIES; i++) {
        if (this.isClosing) break;
        if (!this.isRunning()) {
          logger.warn('Backend process has stopped. Aborting readiness check.');
          break;
        }
        if (Date.now() >= deadline) break;

        let timeoutId: NodeJS.Timeout | undefined;
        try {
          const { host, port } = await this.getBackendConfig();
          const controller = new AbortController();
          timeoutId = setTimeout(() => controller.abort(), BACKEND_STARTUP_CHECK_TIMEOUT_MS);
          await fetch(`http://${host}:${port}`, { signal: controller.signal });
          logger.info(`Backend API is listening on port ${port}.`);
          this.markReady();
          return;
        } catch {
          const retryDelay = Math.min(
            BACKEND_STARTUP_CHECK_INTERVAL_MS * 2 ** Math.min(i, 3),
            BACKEND_STARTUP_CHECK_MAX_INTERVAL_MS,
          );
          const remaining = deadline - Date.now();
          if (remaining > 0) {
            await new Promise((resolve) => setTimeout(resolve, Math.min(retryDelay, remaining)));
          }
        } finally {
          if (timeoutId) clearTimeout(timeoutId);
        }
      }
    } catch (err) {
      logger.warn('Backend health check terminated unexpectedly:', err);
    }

    if (!this.isReadyState && !this.readyError) {
      this.markFailed(new Error('Backend readiness check timed out.'));
    }
  }

  private setupListeners() {
    if (!this.pyProcess) return;

    this.pyProcess.on('error', (err) => {
      const msg = `Failed to execute backend process: ${err.message}`;
      logger.error(msg);
      dialog.showErrorBox('Backend Fatal Error', msg);
      this.markFailed(new Error(msg));
    });

    this.pyProcess.stderr?.on('data', (data) => {
      logger.error(`Backend stderr: ${data.toString().trim()}`);
    });

    this.pyProcess.on('close', (code) => {
      logger.info(`Backend service terminated with code ${code}`);
      this.pyProcess = null;
      if (!this.isReadyState) {
        this.markFailed(new Error(`Backend service terminated with code ${code}`));
      }
    });
  }

  private markReady() {
    if (!this.isClosing && !this.isReadyState && !this.readyError) {
      this.isReadyState = true;
      this.resolveReady();
      logger.info(
        `Backend service initialization completed after ${this.startupStartedAt ? Date.now() - this.startupStartedAt : 0} ms.`,
      );
    }
  }

  private markFailed(error: Error) {
    if (this.isReadyState || this.readyError) return;
    this.readyError = error;
    this.rejectReady(error);
  }

  private terminateCancelledProcess(process: ChildProcess): void {
    if (this.pyProcess === process) this.pyProcess = null;
    if (process.killed) return;
    try {
      process.kill('SIGKILL');
    } catch (error) {
      logger.warn('Failed to terminate a cancelled backend process:', error);
    }
  }

  public async waitForReady(timeoutMs: number = BACKEND_READY_TIMEOUT_MS): Promise<boolean> {
    if (this.isReadyState) return true;
    if (this.readyError) return false;

    let timeoutId: NodeJS.Timeout | undefined;
    const timeoutPromise = new Promise<boolean>((resolve) => {
      timeoutId = setTimeout(() => resolve(false), timeoutMs);
    });

    try {
      return await Promise.race([
        this.readyPromise.then(() => true).catch(() => false),
        timeoutPromise,
      ]);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  }

  public async stop() {
    this.isClosing = true;
    this.markFailed(new Error('Backend startup cancelled because shutdown has started.'));

    if (this.isMockMode) {
      this.isMockMode = false;
      return;
    }
    if (!this.isRunning()) return;

    try {
      const { host, port } = await this.getBackendConfig();
      await fetch(`http://${host}:${port}/api/shutdown`, {
        method: 'POST',
        signal: AbortSignal.timeout(BACKEND_SHUTDOWN_API_TIMEOUT_MS),
      });
    } catch {
      // Ignore error, process might already be closing
    }

    await new Promise<void>((resolve) => {
      if (!this.pyProcess) return resolve();

      const timeout = setTimeout(() => {
        if (this.isRunning()) {
          logger.warn('Backend shutdown timed out, forcing termination.');
          this.pyProcess?.kill('SIGKILL');
        }
        resolve();
      }, BACKEND_SHUTDOWN_TIMEOUT_MS);

      this.pyProcess?.once('close', () => {
        clearTimeout(timeout);
        resolve();
      });
    });

    this.pyProcess = null;
  }
}
