import type { ChildProcess, SpawnOptions } from 'node:child_process';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { createServer, type Server, type Socket } from 'node:net';

const CONNECT_TIMEOUT_MS = 30_000;
const STOP_TIMEOUT_MS = 2_000;
const MAX_PROTOCOL_BUFFER_BYTES = 1024 * 1024;
const PIPE_PREFIX = 'akagi-ng-tun-';
const HELPER_PATH_ENV = 'AKAGI_TUN_HELPER_PATH';
const PIPE_NAME_ENV = 'AKAGI_TUN_PIPE_NAME';

export interface TunHelperLaunchOptions {
  helperPath: string;
  workDir: string;
  configPath: string;
  signal?: AbortSignal;
  onStdout?: (line: string) => void;
  onStderr?: (line: string) => void;
  onUnexpectedExit?: (exitCode: number | null, message?: string) => void;
}

function helperLaunchCancelledError(): Error {
  const error = new Error('TUN helper launch cancelled because application shutdown has started.');
  error.name = 'AbortError';
  return error;
}

function throwIfLaunchCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw helperLaunchCancelledError();
}

function withLaunchCancellation<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(helperLaunchCancelledError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(helperLaunchCancelledError());
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener('abort', onAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener('abort', onAbort);
        reject(error);
      },
    );
  });
}

export type HelperMessage =
  | { type: 'hello'; version: number }
  | { type: 'started'; pid: number }
  | { type: 'log'; stream: 'stdout' | 'stderr'; message: string }
  | { type: 'exited'; exitCode: number | null }
  | { type: 'stopped' }
  | { type: 'error'; message: string };

function decodeBase64Text(value: string): string {
  return Buffer.from(value, 'base64').toString('utf8');
}

export function parseHelperMessage(line: string): HelperMessage {
  const parts = line.split('\t');
  switch (parts[0]) {
    case 'HELLO': {
      const version = Number(parts[1]);
      if (!Number.isSafeInteger(version) || version !== 1)
        throw new Error('Unsupported TUN helper protocol.');
      return { type: 'hello', version };
    }
    case 'STARTED': {
      const pid = Number(parts[1]);
      if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('Invalid TUN helper process id.');
      return { type: 'started', pid };
    }
    case 'LOG':
      if ((parts[1] !== 'stdout' && parts[1] !== 'stderr') || !parts[2]) {
        throw new Error('Invalid TUN helper log message.');
      }
      return { type: 'log', stream: parts[1], message: decodeBase64Text(parts[2]) };
    case 'EXITED': {
      const exitCode = Number(parts[1]);
      return { type: 'exited', exitCode: Number.isSafeInteger(exitCode) ? exitCode : null };
    }
    case 'STOPPED':
      return { type: 'stopped' };
    case 'ERROR':
      return {
        type: 'error',
        message: parts[1] ? decodeBase64Text(parts[1]) : 'TUN helper failed.',
      };
    default:
      throw new Error('Invalid TUN helper response.');
  }
}

export function encodeStartCommand(workDir: string, configPath: string): string {
  const encode = (value: string) => Buffer.from(value, 'utf8').toString('base64');
  return `START\t${encode(workDir)}\t${encode(configPath)}\n`;
}

export function buildElevationCommand(): string {
  return [
    "$ErrorActionPreference = 'Stop'",
    `Start-Process -FilePath $env:${HELPER_PATH_ENV} -ArgumentList @('--pipe', $env:${PIPE_NAME_ENV}) -Verb RunAs -WindowStyle Hidden -Wait`,
  ].join('; ');
}

function pipePath(pipeName: string): string {
  return `\\\\.\\pipe\\${pipeName}`;
}

class HelperChannel {
  private buffer = '';
  private queue: HelperMessage[] = [];
  private waiters: Array<{
    resolve: (message: HelperMessage) => void;
    reject: (error: Error) => void;
  }> = [];
  private terminalError: Error | null = null;

  public constructor(private readonly socket: Socket) {
    socket.setEncoding('utf8');
    socket.on('data', (chunk: string) => this.consume(chunk));
    socket.once('error', (error) => this.close(error));
    socket.once('close', () => this.close(new Error('The privileged TUN helper disconnected.')));
  }

  public send(message: string): void {
    if (this.terminalError || this.socket.destroyed)
      throw new Error('The TUN helper is unavailable.');
    this.socket.write(message);
  }

  public next(timeoutMs: number): Promise<HelperMessage> {
    const queued = this.queue.shift();
    if (queued) return Promise.resolve(queued);
    if (this.terminalError) return Promise.reject(this.terminalError);

    return new Promise<HelperMessage>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.waiters = this.waiters.filter((candidate) => candidate !== waiter);
        reject(new Error('Timed out waiting for the privileged TUN helper.'));
      }, timeoutMs);
      const waiter: {
        resolve: (message: HelperMessage) => void;
        reject: (error: Error) => void;
      } = {
        resolve: (message: HelperMessage) => {
          clearTimeout(timeout);
          resolve(message);
        },
        reject: (error: Error) => {
          clearTimeout(timeout);
          reject(error);
        },
      };
      this.waiters.push(waiter);
    });
  }

  public destroy(): void {
    this.socket.destroy();
  }

  private consume(chunk: string): void {
    this.buffer += chunk;
    if (Buffer.byteLength(this.buffer, 'utf8') > MAX_PROTOCOL_BUFFER_BYTES) {
      this.close(new Error('The TUN helper protocol buffer exceeded its limit.'));
      this.socket.destroy();
      return;
    }

    let newline = this.buffer.indexOf('\n');
    while (newline >= 0) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, '');
      this.buffer = this.buffer.slice(newline + 1);
      if (line) {
        try {
          this.push(parseHelperMessage(line));
        } catch (error) {
          this.close(error instanceof Error ? error : new Error(String(error)));
          this.socket.destroy();
          return;
        }
      }
      newline = this.buffer.indexOf('\n');
    }
  }

  private push(message: HelperMessage): void {
    const waiter = this.waiters.shift();
    if (waiter) waiter.resolve(message);
    else this.queue.push(message);
  }

  private close(error: Error): void {
    if (this.terminalError) return;
    this.terminalError = error;
    for (const waiter of this.waiters.splice(0)) waiter.reject(error);
  }
}

export class WindowsTunSession {
  private running = true;
  private stopping = false;
  private readonly completion: Promise<void>;

  public constructor(
    private readonly channel: HelperChannel,
    private readonly launcher: ChildProcess,
    private readonly options: TunHelperLaunchOptions,
  ) {
    this.completion = this.consumeMessages();
  }

  public isRunning(): boolean {
    return this.running;
  }

  public async stop(): Promise<void> {
    if (!this.running) return;
    this.stopping = true;
    this.channel.send('STOP\n');
    let timeoutId: NodeJS.Timeout | undefined;
    const timeout = new Promise<void>((resolve) => {
      timeoutId = setTimeout(() => {
        this.channel.destroy();
        resolve();
      }, STOP_TIMEOUT_MS);
    });
    await Promise.race([this.completion, timeout]);
    if (timeoutId) clearTimeout(timeoutId);
    this.running = false;
  }

  private async consumeMessages(): Promise<void> {
    try {
      while (this.running) {
        const message = await this.channel.next(24 * 60 * 60 * 1000);
        if (message.type === 'log') {
          if (message.stream === 'stdout') this.options.onStdout?.(message.message);
          else this.options.onStderr?.(message.message);
        } else if (message.type === 'exited') {
          this.running = false;
          if (!this.stopping) this.options.onUnexpectedExit?.(message.exitCode);
        } else if (message.type === 'stopped') {
          this.running = false;
        } else if (message.type === 'error') {
          this.running = false;
          if (!this.stopping) this.options.onUnexpectedExit?.(null, message.message);
        }
      }
    } catch (error) {
      this.running = false;
      if (!this.stopping) {
        this.options.onUnexpectedExit?.(
          this.launcher.exitCode,
          error instanceof Error ? error.message : String(error),
        );
      }
    } finally {
      this.channel.destroy();
    }
  }
}

function listen(server: Server, name: string): Promise<void> {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(pipePath(name), () => {
      server.removeListener('error', reject);
      resolve();
    });
  });
}

function waitForConnection(server: Server, signal?: AbortSignal): Promise<Socket> {
  if (signal?.aborted) return Promise.reject(helperLaunchCancelledError());
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout);
      server.removeListener('connection', onConnection);
      signal?.removeEventListener('abort', onAbort);
    };
    const onConnection = (socket: Socket) => {
      cleanup();
      resolve(socket);
    };
    const onAbort = () => {
      cleanup();
      reject(helperLaunchCancelledError());
    };
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error('Timed out waiting for UAC approval.'));
    }, CONNECT_TIMEOUT_MS);
    server.once('connection', onConnection);
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
  });
}

function launchElevatedHelper(helperPath: string, pipeName: string): ChildProcess {
  const encodedCommand = Buffer.from(buildElevationCommand(), 'utf16le').toString('base64');
  const options: SpawnOptions = {
    env: {
      ...process.env,
      [HELPER_PATH_ENV]: helperPath,
      [PIPE_NAME_ENV]: pipeName,
    },
    shell: false,
    windowsHide: true,
    stdio: ['ignore', 'ignore', 'pipe'],
  };
  return spawn(
    'powershell.exe',
    ['-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encodedCommand],
    options,
  );
}

export async function launchWindowsTunHelper(
  options: TunHelperLaunchOptions,
): Promise<WindowsTunSession> {
  if (process.platform !== 'win32') throw new Error('The privileged TUN helper is Windows-only.');
  throwIfLaunchCancelled(options.signal);

  const pipeName = `${PIPE_PREFIX}${randomBytes(32).toString('hex')}`;
  const server = createServer({ pauseOnConnect: false });
  await listen(server, pipeName);
  if (options.signal?.aborted) {
    server.close();
    throw helperLaunchCancelledError();
  }
  const connectionPromise = waitForConnection(server, options.signal);
  let launcher: ChildProcess;
  try {
    launcher = launchElevatedHelper(options.helperPath, pipeName);
  } catch (error) {
    server.close();
    throw error;
  }
  let launcherError = '';
  launcher.stderr?.setEncoding('utf8');
  launcher.stderr?.on('data', (chunk: string) => {
    launcherError = `${launcherError}${chunk}`.slice(-4096);
  });

  const launcherFailed = new Promise<never>((_resolve, reject) => {
    launcher.once('error', reject);
    launcher.once('close', (code) => {
      if (code !== 0) {
        reject(new Error(launcherError.trim() || 'UAC elevation was cancelled or failed.'));
      }
    });
  });

  let channel: HelperChannel | null = null;
  try {
    const socket = await Promise.race([connectionPromise, launcherFailed]);
    server.close();
    channel = new HelperChannel(socket);
    throwIfLaunchCancelled(options.signal);
    const hello = await withLaunchCancellation(channel.next(CONNECT_TIMEOUT_MS), options.signal);
    if (hello.type !== 'hello') throw new Error('The TUN helper handshake failed.');
    throwIfLaunchCancelled(options.signal);
    channel.send(encodeStartCommand(options.workDir, options.configPath));
    const started = await withLaunchCancellation(channel.next(CONNECT_TIMEOUT_MS), options.signal);
    if (started.type === 'error') throw new Error(started.message);
    if (started.type !== 'started') throw new Error('The TUN helper did not start mihomo.');
    throwIfLaunchCancelled(options.signal);
    return new WindowsTunSession(channel, launcher, options);
  } catch (error) {
    server.close();
    channel?.destroy();
    if (!launcher.killed) {
      try {
        launcher.kill();
      } catch {
        // The launcher may already have exited after UAC cancellation.
      }
    }
    throw error;
  }
}
