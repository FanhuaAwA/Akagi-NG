import { join, resolve } from 'node:path';

import { app } from 'electron';

/**
 * 获取项目的根目录。
 * 在开发模式下，通常是代码仓库的根目录。
 * 在打包模式下，是 Resources 目录的父目录（即包含 bin, config, lib, models 等文件夹的目录）。
 */
export function getProjectRoot(): string {
  return !app.isPackaged ? resolve(__dirname, '../../') : join(process.resourcesPath, '..');
}

/** Return a writable root for settings, logs, secrets, and plugin state. */
export function getUserDataRoot(): string {
  return app.isPackaged ? app.getPath('userData') : getProjectRoot();
}

/**
 * 获取指定路径在项目根目录下的绝对路径。
 */
export function getAssetPath(...paths: string[]): string {
  return join(getProjectRoot(), ...paths);
}

/**
 * 判断一个窗口对象是否处于完全可用、可交互且未被销毁的状态
 */
export function isSafeWindow(win?: Electron.BrowserWindow | null): win is Electron.BrowserWindow {
  return !!win && !win.isDestroyed();
}

/**
 * 向一个可能处于边缘销毁状态的窗口发送 IPC 消息
 */
export function safeSend(
  win: Electron.BrowserWindow | null | undefined,
  channel: string,
  ...args: unknown[]
) {
  if (isSafeWindow(win) && !win.webContents.isDestroyed()) {
    win.webContents.send(channel, ...args);
  }
}
