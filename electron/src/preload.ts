import type { IpcRendererEvent } from 'electron';
import { contextBridge, ipcRenderer } from 'electron';

type Unsubscribe = () => void;

function subscribe<TArgs extends readonly unknown[]>(
  channel: string,
  callback: (...args: TArgs) => void,
): Unsubscribe {
  const listener = (_event: IpcRendererEvent, ...args: unknown[]) => {
    callback(...(args as unknown as TArgs));
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('electron', {
  platform: process.platform,
  getStartupConfig: () => ipcRenderer.invoke('get-startup-config'),
  waitForBackend: (timeoutMs?: number) => ipcRenderer.invoke('wait-for-backend', timeoutMs),
  checkResourceStatus: () => ipcRenderer.invoke('check-resource-status'),
  startGame: (options: { url?: string; useMitm?: boolean; platform?: string }) =>
    ipcRenderer.invoke('start-game', options),
  requestShutdown: () => ipcRenderer.invoke('request-shutdown'),
  closeDashboard: () => ipcRenderer.invoke('close-window'),
  minimizeDashboard: () => ipcRenderer.invoke('minimize-window'),
  toggleDashboardMaximized: () => ipcRenderer.invoke('maximize-window'),
  isDashboardMaximized: () => ipcRenderer.invoke('is-window-maximized'),
  toggleHud: (show: boolean) => ipcRenderer.invoke('toggle-hud', show),
  getHudClickThroughStatus: () => ipcRenderer.invoke('hud-click-through-status'),
  setHudClickThrough: (enabled: boolean) => ipcRenderer.invoke('hud-set-click-through', enabled),
  setHudControlsInteractive: (interactive: boolean) =>
    ipcRenderer.invoke('hud-set-controls-interactive', interactive),
  reconcileDesktop: () => ipcRenderer.invoke('desktop-reconcile'),
  getMihomoStatus: () => ipcRenderer.invoke('mihomo-status'),
  reconcileMihomo: (gameProxyChanged = false) =>
    ipcRenderer.invoke('mihomo-reconcile', gameProxyChanged),
  stopMihomo: () => ipcRenderer.invoke('mihomo-stop'),
  updateLiqi: () => ipcRenderer.invoke('update-liqi'),
  updateLocale: (locale: string) => ipcRenderer.invoke('update-locale', locale),
  openExternal: (url: string) => ipcRenderer.invoke('open-external', url),
  onExitAnimationStart: (callback: () => void) => subscribe('exit-animation-start', callback),
  onLocaleChanged: (callback: (locale: string) => void) => subscribe('locale-changed', callback),
  onHudVisibilityChanged: (callback: (visible: boolean) => void) =>
    subscribe('hud-visibility-changed', callback),
  onHudClickThroughChanged: (callback: (status: { enabled: boolean }) => void) =>
    subscribe('hud-click-through-changed', callback),
  onRequestAppQuit: (callback: () => void) => subscribe('request-app-quit', callback),
  onUpdateAvailable: (callback: (version: string) => void) =>
    subscribe('app:update-available', callback),
  onDashboardMaximizedChanged: (callback: (maximized: boolean) => void) =>
    subscribe('window-state-changed', callback),
});
