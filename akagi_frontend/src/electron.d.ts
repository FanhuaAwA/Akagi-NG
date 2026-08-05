import type { ResourceStatus, Settings } from '@/types';

export interface BackendEndpoint {
  host: string;
  port: number;
}

export interface StartupConfig extends BackendEndpoint {
  settings: Settings;
}

export interface HudClickThroughStatus {
  enabled: boolean;
  ignoreMouseEvents?: boolean;
}

export interface MihomoRuntimeStatus {
  running: boolean;
  error?: string;
  gameWindowClosed?: boolean;
}

export interface StartGameOptions {
  url?: string;
  useMitm?: boolean;
  platform?: string;
}

export interface ElectronApi {
  readonly platform: NodeJS.Platform;
  getStartupConfig: () => Promise<StartupConfig>;
  waitForBackend: (timeoutMs?: number) => Promise<BackendEndpoint>;
  checkResourceStatus: () => Promise<ResourceStatus>;
  startGame: (options: StartGameOptions) => Promise<boolean>;
  requestShutdown: () => Promise<boolean>;
  closeDashboard: () => Promise<boolean>;
  minimizeDashboard: () => Promise<boolean>;
  toggleDashboardMaximized: () => Promise<boolean>;
  isDashboardMaximized: () => Promise<boolean>;
  toggleHud: (show: boolean) => Promise<HudClickThroughStatus>;
  getHudClickThroughStatus: () => Promise<HudClickThroughStatus>;
  setHudClickThrough: (enabled: boolean) => Promise<HudClickThroughStatus>;
  setHudControlsInteractive: (interactive: boolean) => Promise<HudClickThroughStatus>;
  reconcileDesktop: () => Promise<HudClickThroughStatus>;
  getMihomoStatus: () => Promise<MihomoRuntimeStatus>;
  reconcileMihomo: (gameProxyChanged?: boolean) => Promise<MihomoRuntimeStatus>;
  stopMihomo: () => Promise<MihomoRuntimeStatus>;
  updateLiqi: () => Promise<boolean>;
  updateLocale: (locale: string) => Promise<boolean>;
  openExternal: (url: string) => Promise<boolean>;
  onExitAnimationStart: (callback: () => void) => () => void;
  onLocaleChanged: (callback: (locale: string) => void) => () => void;
  onHudVisibilityChanged: (callback: (visible: boolean) => void) => () => void;
  onHudClickThroughChanged: (callback: (status: HudClickThroughStatus) => void) => () => void;
  onRequestAppQuit: (callback: () => void) => () => void;
  onUpdateAvailable: (callback: (version: string) => void) => () => void;
  onDashboardMaximizedChanged: (callback: (maximized: boolean) => void) => () => void;
}

declare global {
  interface Window {
    electron: ElectronApi;
  }
}
