import type { ChildProcess } from 'node:child_process';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { delimiter, join } from 'node:path';

import { app, dialog } from 'electron';

interface AppSettings {
  server?: {
    host?: string;
    port?: number;
  };
  mitm?: {
    host?: string;
    port?: number;
  };
}

import {
  BACKEND_READY_TIMEOUT_MS,
  BACKEND_SHUTDOWN_API_TIMEOUT_MS,
  BACKEND_SHUTDOWN_TIMEOUT_MS,
  BACKEND_STARTUP_CHECK_INTERVAL_MS,
  BACKEND_STARTUP_CHECK_RETRIES,
  BACKEND_STARTUP_CHECK_TIMEOUT_MS,
} from './constants.js';
import type { ResourceStatus } from './resource-validator.js';
import { ResourceValidator } from './resource-validator.js';
import { getAssetPath, getProjectRoot } from './utils.js';

export class BackendManager {
  private pyProcess: ChildProcess | null = null;
  private validator: ResourceValidator;
  private isReadyState: boolean = false;
  private readyPromise: Promise<void>;
  private resolveReady!: () => void;
  private rejectReady!: (reason?: Error) => void;
  private isMockMode: boolean = false;

  private async getSettings(): Promise<AppSettings> {
    try {
      const settingsPath = getAssetPath('config', 'settings.json');
      const fileContent = await readFile(settingsPath, 'utf8');
      return JSON.parse(fileContent) as AppSettings;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') {
        console.warn(
          '[BackendManager] Failed to read settings.json for config:',
          err instanceof Error ? err.message : String(err),
        );
      }
    }
    return {};
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

  public async getMitmConfig(): Promise<{ host: string; port: number }> {
    const settings = await this.getSettings();
    return {
      host: settings.mitm?.host ?? '127.0.0.1',
      port: settings.mitm?.port ?? 6789,
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

    this.validator = new ResourceValidator(getProjectRoot());
  }

  public async getResourceStatus(): Promise<ResourceStatus> {
    return await this.validator.validate();
  }

  public start() {
    if (this.pyProcess) {
      console.log('Backend already running.');
      return;
    }

    const isDev = !app.isPackaged;

    if (process.argv.includes('--mock')) {
      this.isMockMode = true;
      this.startMockBackend();
    } else if (isDev) {
      this.startDevBackend();
    } else {
      this.startProdBackend();
    }
  }

  private startDevBackend() {
    console.log('Starting backend in DEV mode...');

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
      console.error(`[BackendManager] ${errorMsg}`);
      dialog.showErrorBox('Backend Initialization Failed', errorMsg);
      return;
    }

    const env = {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      AKAGI_GUI_MODE: '1',
      PYTHONPATH: process.env.PYTHONPATH
        ? `${backendRoot}${delimiter}${process.env.PYTHONPATH}`
        : backendRoot,
    };

    this.pyProcess = spawn(pythonExecutable, ['-m', 'akagi_ng'], {
      cwd: projectRoot,
      env: env,
    });

    this.setupListeners();
    this.startHealthCheck();
  }

  private startMockBackend() {
    console.log('[BackendManager] Starting backend in MOCK mode...');
    this.markReady();
  }

  private startProdBackend() {
    console.log('Starting backend in PROD mode...');

    const isWin = process.platform === 'win32';
    const bundleDir = getAssetPath('bin');
    const pythonExecutable = join(bundleDir, 'python', isWin ? 'akagi-ng.exe' : 'bin/akagi-ng');

    if (!existsSync(pythonExecutable)) {
      const msg = `Portable Python not found at ${pythonExecutable}`;
      console.error(`[BackendManager] ${msg}`);
      dialog.showErrorBox('Startup Error', msg);
      return;
    }

    try {
      this.pyProcess = spawn(pythonExecutable, ['-m', 'akagi_ng'], {
        cwd: getProjectRoot(),
        env: {
          ...process.env,
          PYTHONPATH: join(bundleDir, 'app_packages'),
          PYTHONUNBUFFERED: '1',
          AKAGI_GUI_MODE: '1',
        },
      });

      this.setupListeners();
      this.startHealthCheck();
    } catch (e) {
      const msg = `Backend initialization failed: ${e instanceof Error ? e.message : String(e)}`;
      console.error(`[BackendManager] ${msg}`);
      dialog.showErrorBox('Startup Error', msg);
    }
  }

  private async startHealthCheck() {
    try {
      for (let i = 0; i < BACKEND_STARTUP_CHECK_RETRIES; i++) {
        if (!this.isRunning()) {
          console.warn('[BackendManager] Backend process has stopped. Aborting readiness check.');
          break;
        }
        try {
          const { host, port } = await this.getBackendConfig();
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), BACKEND_STARTUP_CHECK_TIMEOUT_MS);
          await fetch(`http://${host}:${port}`, { signal: controller.signal });
          clearTimeout(timeoutId);
          console.log(`[BackendManager] API port ${port} is ready to traffic.`);
          this.markReady();
          break;
        } catch {
          await new Promise((resolve) => setTimeout(resolve, BACKEND_STARTUP_CHECK_INTERVAL_MS));
        }
      }
    } catch (err) {
      console.warn('[BackendManager] Health check unexpected termination:', err);
    }
  }

  private setupListeners() {
    if (!this.pyProcess) return;

    this.pyProcess.on('error', (err) => {
      const msg = `Failed to execute backend process: ${err.message}`;
      console.error(`[BackendManager] ${msg}`);
      dialog.showErrorBox('Backend Fatal Error', msg);
    });

    this.pyProcess.stdout?.on('data', (data) => {
      console.log(`${data.toString().trim()}`);
    });

    this.pyProcess.stderr?.on('data', (data) => {
      console.error(`[Backend Error]: ${data.toString().trim()}`);
    });

    this.pyProcess.on('close', (code) => {
      console.log(`Backend process exited with code ${code}`);
      this.pyProcess = null;
      if (!this.isReadyState) {
        this.rejectReady(new Error(`Backend exited with code ${code}`));
      }
    });
  }

  private markReady() {
    if (!this.isReadyState) {
      this.isReadyState = true;
      this.resolveReady();
      console.log('[BackendManager] Backend is marked as READY.');
    }
  }

  public async waitForReady(timeoutMs: number = BACKEND_READY_TIMEOUT_MS): Promise<boolean> {
    if (this.isReadyState) return true;

    const timeoutPromise = new Promise<boolean>((resolve) => {
      setTimeout(() => resolve(false), timeoutMs);
    });

    return Promise.race([this.readyPromise.then(() => true).catch(() => false), timeoutPromise]);
  }

  public async stop() {
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
          console.warn('[BackendManager] Shutdown timeout, forcing exit');
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
