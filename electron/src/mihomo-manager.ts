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
import { launchWindowsTunHelper, type WindowsTunSession } from './windows-tun-helper.js';

const logger = createLogger('MihomoManager');
const STARTUP_TIMEOUT_MS = 8_000;
const STARTUP_CHECK_INTERVAL_MS = 200;

export interface MihomoStatus {
  enabled: boolean;
  running: boolean;
  error?: string;
}

export class MihomoManager {
  private session: WindowsTunSession | null = null;
  private controllerUrl = '';
  private controllerSecret = '';
  private lastError: string | undefined;
  private isStopping = false;
  private lifecycle: Promise<void> = Promise.resolve();

  public constructor(private readonly backendManager: BackendManager) {}

  public isRunning(): boolean {
    return this.session?.isRunning() ?? false;
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
    return this.enqueue(() => this.doStartIfEnabled());
  }

  private async doStartIfEnabled(): Promise<MihomoStatus> {
    const config = await this.backendManager.getProxyConfig();
    if (!config.mihomo.enabled) return this.getStatus();
    return this.doStart();
  }

  public async reconcile(): Promise<MihomoStatus> {
    return this.enqueue(() => this.doReconcile());
  }

  private async doReconcile(): Promise<MihomoStatus> {
    await this.doStop();
    const config = await this.backendManager.getProxyConfig();
    if (!config.mihomo.enabled) {
      this.lastError = undefined;
      return this.getStatus();
    }
    return this.doStart();
  }

  public async start(): Promise<MihomoStatus> {
    return this.enqueue(() => this.doStart());
  }

  private async doStart(): Promise<MihomoStatus> {
    if (this.isRunning()) return this.getStatus();
    this.lastError = undefined;
    this.isStopping = false;

    const config = await this.backendManager.getProxyConfig();
    if (!config.mihomo.enabled) {
      return this.fail('未启用内置 mihomo TUN，已拒绝提权请求。');
    }

    if (process.platform !== 'win32') {
      return this.fail('当前内置 mihomo 仅打包了 Windows x64 内核。');
    }

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

    const helperPath = app.isPackaged
      ? getAssetPath('assets', 'privileged', 'AkagiNg.TunHelper.exe')
      : getAssetPath('build', 'privileged', 'AkagiNg.TunHelper.exe');
    if (!existsSync(helperPath)) {
      return this.fail(`找不到 TUN 权限助手：${helperPath}`);
    }

    logger.info('Requesting elevation for the isolated mihomo TUN helper.');
    try {
      this.session = await launchWindowsTunHelper({
        helperPath,
        workDir,
        configPath,
        onStdout: (line) => logger.info(`mihomo: ${line.trim()}`),
        onStderr: (line) => logger.warn(`mihomo stderr: ${line.trim()}`),
        onUnexpectedExit: (code, message) => {
          this.session = null;
          if (!this.isStopping) {
            this.lastError = message
              ? `mihomo 权限助手异常退出：${message}`
              : `mihomo 异常退出（代码 ${String(code)}）。`;
            logger.error(this.lastError);
          }
          this.isStopping = false;
        },
      });
    } catch (error) {
      this.session = null;
      return this.fail(
        error instanceof Error ? `无法启动 mihomo TUN：${error.message}` : '无法启动 mihomo TUN。',
      );
    }

    try {
      await this.waitUntilReady();
      logger.info(`mihomo controller is ready at ${this.controllerUrl}`);
      return this.getStatus();
    } catch (error) {
      await this.doStop();
      return this.fail(error instanceof Error ? error.message : String(error));
    }
  }

  public async stop(): Promise<void> {
    return this.enqueue(() => this.doStop());
  }

  private async doStop(): Promise<void> {
    const currentSession = this.session;
    if (!currentSession || !currentSession.isRunning()) {
      this.session = null;
      return;
    }

    logger.info('Stopping mihomo core...');
    this.isStopping = true;
    await currentSession.stop();
    this.session = null;
    this.isStopping = false;
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.lifecycle.then(operation, operation);
    this.lifecycle = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
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
