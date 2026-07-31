export type OverlayMode = 'standard' | 'advanced';
export type AdvancedOverlayHost = 'auto' | 'discord' | 'protected';

export interface DesktopConfig {
  overlayMode: OverlayMode;
  advancedHost: AdvancedOverlayHost;
  captureProtection: boolean;
  privacyMode: boolean;
  trayVisible: boolean;
  startHidden: boolean;
  restoreShortcut: string;
}

export const DEFAULT_DESKTOP_CONFIG: DesktopConfig = {
  overlayMode: 'standard',
  advancedHost: 'auto',
  captureProtection: true,
  privacyMode: false,
  trayVisible: true,
  startHidden: false,
  restoreShortcut: 'CommandOrControl+Shift+A',
};
