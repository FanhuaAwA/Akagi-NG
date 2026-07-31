import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { app } from 'electron';

import type { BackendManager } from './backend-manager.js';
import type { DesktopConfig } from './desktop-config.js';
import { createLogger } from './logger.js';
import { getAssetPath, getProjectRoot } from './utils.js';

export interface AdvancedOverlayStatus {
  running: boolean;
  host: 'discord' | 'protected';
  fallbackUsed: boolean;
  error?: string;
}

const logger = createLogger('AdvancedOverlay');

export class AdvancedOverlayManager {
  private process: ChildProcessWithoutNullStreams | null = null;
  private expectedStop = false;
  private lastStatus: AdvancedOverlayStatus = {
    running: false,
    host: 'protected',
    fallbackUsed: false,
  };

  constructor(private backendManager: BackendManager) {}

  public getStatus(): AdvancedOverlayStatus {
    return { ...this.lastStatus };
  }

  public isRunning(): boolean {
    return !!this.process && this.process.exitCode === null && !this.process.killed;
  }

  public async start(config: DesktopConfig): Promise<AdvancedOverlayStatus> {
    if (process.platform !== 'win32') {
      return this.fail('Advanced overlay is only available on Windows.');
    }

    if (this.isRunning()) {
      return this.getStatus();
    }

    const executable = this.getExecutablePath();
    if (!existsSync(executable)) {
      return this.fail(`Advanced overlay executable was not found: ${executable}`);
    }

    const backend = await this.backendManager.getBackendConfig();
    const requestedHost =
      config.advancedHost === 'auto'
        ? config.captureProtection
          ? 'protected'
          : 'discord'
        : config.advancedHost;
    const host = requestedHost === 'discord' ? 'discord' : 'protected';
    const fallbackUsed = config.advancedHost === 'auto' && host === 'protected';
    const clientId = `advanced-overlay-${process.pid}-${Date.now()}`;
    const args = [
      `--sse=http://${backend.host}:${backend.port}/sse?clientId=${clientId}`,
      `--host=${host}`,
      `--capture-protection=${config.captureProtection ? 'true' : 'false'}`,
      '--parent-pid=' + process.pid,
    ];

    logger.info(`Starting ${executable} with host=${host}`);
    this.expectedStop = false;

    try {
      const child = spawn(executable, args, {
        cwd: getProjectRoot(),
        windowsHide: true,
        stdio: 'pipe',
      });
      this.process = child;
    } catch (error) {
      return this.fail(error instanceof Error ? error.message : String(error));
    }

    const child = this.process;
    if (!child) return this.fail('Advanced overlay process did not start.');
    child.stdout.on('data', (data: Buffer) => {
      const line = data.toString().trim();
      if (line) logger.info(line);
    });
    child.stderr.on('data', (data: Buffer) => {
      const line = data.toString().trim();
      if (line) logger.warn(line);
    });
    child.on('error', (error) => {
      this.fail(error.message);
    });
    child.on('close', (code) => {
      const unexpected = !this.expectedStop;
      this.process = null;
      this.lastStatus = {
        running: false,
        host,
        fallbackUsed,
        error: unexpected ? `Advanced overlay exited with code ${code ?? 'unknown'}.` : undefined,
      };
      logger.info(`Advanced overlay exited with code ${code ?? 'unknown'}`);
    });

    this.lastStatus = { running: true, host, fallbackUsed };
    return this.getStatus();
  }

  public async restart(config: DesktopConfig): Promise<AdvancedOverlayStatus> {
    await this.stop();
    return this.start(config);
  }

  public async stop(): Promise<void> {
    const child = this.process;
    if (!child) return;

    this.expectedStop = true;
    child.kill();
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        if (child.exitCode === null) child.kill('SIGKILL');
        resolve();
      }, 2000);
      child.once('close', () => {
        clearTimeout(timeout);
        resolve();
      });
    });
    this.process = null;
    this.lastStatus = { ...this.lastStatus, running: false };
  }

  private getExecutablePath(): string {
    if (app.isPackaged) {
      return getAssetPath('bin', 'AkagiAdvancedOverlay.exe');
    }
    return join(getProjectRoot(), 'dist', 'native', 'AkagiAdvancedOverlay.exe');
  }

  private fail(error: string): AdvancedOverlayStatus {
    logger.error(error);
    this.lastStatus = {
      running: false,
      host: 'protected',
      fallbackUsed: true,
      error,
    };
    return this.getStatus();
  }
}
