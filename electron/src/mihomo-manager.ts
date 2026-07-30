import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { app } from 'electron';

import type { BackendManager } from './backend-manager.js';
import { createLogger } from './logger.js';
import { buildMihomoConfig } from './mihomo-config.js';
import { getAssetPath } from './utils.js';

const logger = createLogger('MihomoManager');
const STARTUP_TIMEOUT_MS = 8_000;
const STARTUP_CHECK_INTERVAL_MS = 200;
const SHUTDOWN_TIMEOUT_MS = 3_000;

export interface MihomoStatus {
  enabled: boolean;
  running: boolean;
  error?: string;
}

export class MihomoManager {
  private process: ChildProcessWithoutNullStreams | null = null;
  private controllerUrl = '';
  private controllerSecret = '';
  private lastError: string | undefined;
  private isStopping = false;
  private reconcilePromise: Promise<MihomoStatus> | null = null;

  public constructor(private readonly backendManager: BackendManager) {}

  public isRunning(): boolean {
    return this.process !== null && this.process.exitCode === null && !this.process.killed;
  }

  public async getStatus(): Promise<MihomoStatus> {
    const { mihomo } = await this.backendManager.getProxyConfig();
    return {
      enabled: mihomo.enabled,
      running: this.isRunning(),
      ...(this.lastError ? { error: this.lastError } : {}),
    };
  }

  public async startIfEnabled(): Promise<MihomoStatus> {
    const config = await this.backendManager.getProxyConfig();
    if (!config.mihomo.enabled) return this.getStatus();
    return this.start();
  }

  public async reconcile(): Promise<MihomoStatus> {
    if (this.reconcilePromise) return this.reconcilePromise;
    this.reconcilePromise = this.doReconcile().finally(() => {
      this.reconcilePromise = null;
    });
    return this.reconcilePromise;
  }

  private async doReconcile(): Promise<MihomoStatus> {
    await this.stop();
    const config = await this.backendManager.getProxyConfig();
    if (!config.mihomo.enabled) {
      this.lastError = undefined;
      return this.getStatus();
    }
    return this.start();
  }

  public async start(): Promise<MihomoStatus> {
    if (this.isRunning()) return this.getStatus();
    this.lastError = undefined;
    this.isStopping = false;

    if (process.platform !== 'win32') {
      return this.fail('当前内置 mihomo 仅打包了 Windows x64 内核。');
    }

    const config = await this.backendManager.getProxyConfig();
    if (!config.mitm.enabled) {
      return this.fail('mihomo 需要先启用 Akagi-NG 外部代理（MITM）。');
    }

    const binaryPath = getAssetPath('assets', 'mihomo', 'windows-x64', 'mihomo.exe');
    if (!existsSync(binaryPath)) {
      return this.fail(`找不到 mihomo 内核：${binaryPath}`);
    }

    const workDir = join(app.getPath('userData'), 'mihomo');
    const configPath = join(workDir, 'config.yaml');
    await mkdir(workDir, { recursive: true });

    this.controllerSecret = randomBytes(24).toString('hex');
    this.controllerUrl = `http://127.0.0.1:${config.mihomo.controllerPort}`;
    const mitmHost =
      config.mitm.host === '0.0.0.0' || config.mitm.host === '::' ? '127.0.0.1' : config.mitm.host;
    const generatedConfig = buildMihomoConfig({
      mitmHost,
      mitmPort: config.mitm.port,
      mixedPort: config.mihomo.mixedPort,
      controllerPort: config.mihomo.controllerPort,
      strictRoute: config.mihomo.strictRoute,
      secret: this.controllerSecret,
    });
    await writeFile(configPath, JSON.stringify(generatedConfig, null, 2), 'utf8');

    try {
      await this.validateConfig(binaryPath, workDir, configPath);
    } catch (error) {
      return this.fail(error instanceof Error ? error.message : String(error));
    }

    logger.info(`Starting mihomo core from ${binaryPath}`);
    this.process = spawn(binaryPath, ['-d', workDir, '-f', configPath], {
      cwd: workDir,
      windowsHide: true,
    });

    this.process.stdout.on('data', (data) => {
      logger.info(`mihomo: ${data.toString().trim()}`);
    });
    this.process.stderr.on('data', (data) => {
      logger.warn(`mihomo stderr: ${data.toString().trim()}`);
    });
    this.process.on('error', (error) => {
      this.lastError = `mihomo 启动失败：${error.message}`;
      logger.error(this.lastError);
    });
    this.process.on('close', (code) => {
      const wasStopping = this.isStopping;
      this.isStopping = false;
      if (!wasStopping && code !== 0 && !this.lastError) {
        this.lastError = `mihomo 异常退出（代码 ${String(code)}）。请确认以管理员身份运行，并允许其通过 Windows 防火墙。`;
      }
      logger.info(`mihomo terminated with code ${String(code)}`);
      this.process = null;
    });

    try {
      await this.waitUntilReady();
      logger.info(`mihomo controller is ready at ${this.controllerUrl}`);
      return this.getStatus();
    } catch (error) {
      await this.stop();
      return this.fail(error instanceof Error ? error.message : String(error));
    }
  }

  public async stop(): Promise<void> {
    const currentProcess = this.process;
    if (!currentProcess || currentProcess.exitCode !== null) {
      this.process = null;
      return;
    }

    logger.info('Stopping mihomo core...');
    this.isStopping = true;
    currentProcess.kill('SIGTERM');
    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        if (currentProcess.exitCode === null) currentProcess.kill('SIGKILL');
        resolve();
      }, SHUTDOWN_TIMEOUT_MS);
      currentProcess.once('close', () => {
        clearTimeout(timeout);
        resolve();
      });
    });
    this.process = null;
  }

  private async validateConfig(
    binaryPath: string,
    workDir: string,
    configPath: string,
  ): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(binaryPath, ['-t', '-d', workDir, '-f', configPath], {
        cwd: workDir,
        windowsHide: true,
      });
      let output = '';
      child.stdout.on('data', (data) => (output += data.toString()));
      child.stderr.on('data', (data) => (output += data.toString()));
      child.on('error', reject);
      child.on('close', (code) => {
        if (code === 0) resolve();
        else reject(new Error(`mihomo 配置校验失败（代码 ${String(code)}）：${output.trim()}`));
      });
    });
  }

  private async waitUntilReady(): Promise<void> {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (!this.isRunning()) {
        throw new Error(
          this.lastError ??
            'mihomo 在控制端口就绪前退出。Windows TUN 通常需要管理员权限和防火墙许可。',
        );
      }
      try {
        const response = await fetch(`${this.controllerUrl}/version`, {
          headers: { Authorization: `Bearer ${this.controllerSecret}` },
          signal: AbortSignal.timeout(500),
        });
        if (response.ok) return;
      } catch {
        // Keep polling until timeout or process exit.
      }
      await new Promise((resolve) => setTimeout(resolve, STARTUP_CHECK_INTERVAL_MS));
    }
    throw new Error('mihomo 启动超时：控制端口未就绪，请检查端口占用、管理员权限和防火墙。');
  }

  private async fail(message: string): Promise<MihomoStatus> {
    this.lastError = message;
    logger.error(message);
    return this.getStatus();
  }
}
