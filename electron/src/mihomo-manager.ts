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

class MihomoStartupCancelledError extends Error {
  public constructor() {
    super('mihomo startup cancelled because application shutdown has started.');
    this.name = 'AbortError';
  }
}

function waitForStartupDelay(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(new MihomoStartupCancelledError());
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timeoutId);
      reject(new MihomoStartupCancelledError());
    };
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

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
  private isClosing = false;
  private readonly shutdownController = new AbortController();
  private sessionStopPromise: Promise<void> | null = null;
  private lifecycle: Promise<void> = Promise.resolve();

  public constructor(private readonly backendManager: BackendManager) {}

  public isRunning(): boolean {
    return this.session?.isRunning() ?? false;
  }

  public beginShutdown(): void {
    if (this.isClosing) return;
    this.isClosing = true;
    this.shutdownController.abort();

    const currentSession = this.session;
    if (currentSession?.isRunning()) void this.stopSession(currentSession);
  }

  private assertStartupAllowed(): void {
    if (this.isClosing) throw new MihomoStartupCancelledError();
  }

  private shutdownStatus(): MihomoStatus {
    return { enabled: false, running: this.isRunning() };
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
    if (this.isClosing) return this.shutdownStatus();
    const config = await this.backendManager.getProxyConfig();
    if (this.isClosing) return this.shutdownStatus();
    if (!config.mihomo.enabled) return this.getStatus();
    return this.doStart();
  }

  public async reconcile(): Promise<MihomoStatus> {
    return this.enqueue(() => this.doReconcile());
  }

  private async doReconcile(): Promise<MihomoStatus> {
    if (this.isClosing) return this.shutdownStatus();
    await this.doStop();
    if (this.isClosing) return this.shutdownStatus();
    const config = await this.backendManager.getProxyConfig();
    if (this.isClosing) return this.shutdownStatus();
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
    if (this.isClosing) return this.shutdownStatus();
    if (this.isRunning()) return this.getStatus();
    this.lastError = undefined;
    this.isStopping = false;

    const config = await this.backendManager.getProxyConfig();
    if (this.isClosing) return this.shutdownStatus();
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
    if (this.isClosing) return this.shutdownStatus();

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
    if (this.isClosing) return this.shutdownStatus();

    try {
      await this.validateConfig(binaryPath, workDir, configPath, this.shutdownController.signal);
    } catch (error) {
      if (this.isClosing) return this.shutdownStatus();
      return this.fail(error instanceof Error ? error.message : String(error));
    }
    if (this.isClosing) return this.shutdownStatus();

    const helperPath = app.isPackaged
      ? getAssetPath('assets', 'privileged', 'AkagiNg.TunHelper.exe')
      : getAssetPath('build', 'privileged', 'AkagiNg.TunHelper.exe');
    if (!existsSync(helperPath)) {
      return this.fail(`找不到 TUN 权限助手：${helperPath}`);
    }

    logger.info('Requesting elevation for the isolated mihomo TUN helper.');
    try {
      this.assertStartupAllowed();
      const launchedSession = await launchWindowsTunHelper({
        helperPath,
        workDir,
        configPath,
        signal: this.shutdownController.signal,
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
      this.session = launchedSession;
      if (this.isClosing) {
        await this.stopSession(launchedSession);
        return this.shutdownStatus();
      }
    } catch (error) {
      this.session = null;
      if (this.isClosing || error instanceof MihomoStartupCancelledError) {
        return this.shutdownStatus();
      }
      return this.fail(
        error instanceof Error ? `无法启动 mihomo TUN：${error.message}` : '无法启动 mihomo TUN。',
      );
    }

    try {
      await this.waitUntilReady();
      this.assertStartupAllowed();
      logger.info(`mihomo controller is ready at ${this.controllerUrl}`);
      return this.getStatus();
    } catch (error) {
      await this.doStop();
      if (this.isClosing || error instanceof MihomoStartupCancelledError) {
        return this.shutdownStatus();
      }
      return this.fail(error instanceof Error ? error.message : String(error));
    }
  }

  public async stop(): Promise<void> {
    return this.enqueue(() => this.doStop());
  }

  private async doStop(): Promise<void> {
    if (this.sessionStopPromise) {
      await this.sessionStopPromise;
      return;
    }

    const currentSession = this.session;
    if (!currentSession || !currentSession.isRunning()) {
      this.session = null;
      return;
    }

    await this.stopSession(currentSession);
  }

  private async stopSession(currentSession: WindowsTunSession): Promise<void> {
    if (this.sessionStopPromise) {
      await this.sessionStopPromise;
      return;
    }
    logger.info('Stopping mihomo core...');
    this.isStopping = true;
    const stopPromise = (async () => {
      try {
        await currentSession.stop();
      } catch (error) {
        logger.warn('Failed to stop mihomo session cleanly:', error);
      } finally {
        if (this.session === currentSession) this.session = null;
        this.isStopping = false;
      }
    })();
    this.sessionStopPromise = stopPromise;
    try {
      await stopPromise;
    } finally {
      if (this.sessionStopPromise === stopPromise) this.sessionStopPromise = null;
    }
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
    signal: AbortSignal,
  ): Promise<void> {
    if (signal.aborted) throw new MihomoStartupCancelledError();
    await new Promise<void>((resolve, reject) => {
      const child = spawn(binaryPath, ['-t', '-d', workDir, '-f', configPath], {
        cwd: workDir,
        windowsHide: true,
      });
      let settled = false;
      let output = '';
      const finish = (error?: Error) => {
        if (settled) return;
        settled = true;
        signal.removeEventListener('abort', onAbort);
        if (error) reject(error);
        else resolve();
      };
      const onAbort = () => {
        try {
          child.kill('SIGKILL');
        } catch {
          // The validation process may already have exited.
        }
        finish(new MihomoStartupCancelledError());
      };
      child.stdout.on('data', (data) => (output += data.toString()));
      child.stderr.on('data', (data) => (output += data.toString()));
      child.on('error', (error) => finish(error));
      child.on('close', (code) => {
        if (code === 0) finish();
        else finish(new Error(`mihomo 配置校验失败（代码 ${String(code)}）：${output.trim()}`));
      });
      signal.addEventListener('abort', onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  }

  private async waitUntilReady(): Promise<void> {
    const deadline = Date.now() + STARTUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      this.assertStartupAllowed();
      if (!this.isRunning()) {
        throw new Error(
          this.lastError ??
            'mihomo 在控制端口就绪前退出。Windows TUN 通常需要管理员权限和防火墙许可。',
        );
      }
      try {
        const response = await fetch(`${this.controllerUrl}/version`, {
          headers: { Authorization: `Bearer ${this.controllerSecret}` },
          signal: AbortSignal.any([AbortSignal.timeout(500), this.shutdownController.signal]),
        });
        this.assertStartupAllowed();
        if (response.ok) return;
      } catch {
        this.assertStartupAllowed();
        // Keep polling until timeout or process exit.
      }
      await waitForStartupDelay(STARTUP_CHECK_INTERVAL_MS, this.shutdownController.signal);
    }
    throw new Error('mihomo 启动超时：控制端口未就绪，请检查端口占用、管理员权限和防火墙。');
  }

  private async fail(message: string): Promise<MihomoStatus> {
    this.lastError = message;
    logger.error(message);
    return this.getStatus();
  }
}
