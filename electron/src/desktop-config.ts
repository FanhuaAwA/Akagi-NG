export type OverlayMode = 'standard' | 'advanced';
export type AdvancedOverlayHost = 'auto' | 'discord' | 'protected';

export interface DesktopConfig {
  overlayMode: OverlayMode;
  advancedHost: AdvancedOverlayHost;
  captureProtection: boolean;
  trayVisible: boolean;
}

export const DEFAULT_DESKTOP_CONFIG: DesktopConfig = {
  overlayMode: 'standard',
  advancedHost: 'auto',
  captureProtection: true,
  trayVisible: true,
};

export function getDashboardWindowPolicy(config: DesktopConfig) {
  return {
    skipTaskbar: false,
    startVisible: true,
    contentProtection: config.captureProtection,
    closeAction: config.trayVisible ? ('hide' as const) : ('minimize' as const),
  };
}
