export interface DesktopConfig {
  captureProtection: boolean;
  trayVisible: boolean;
}

export const DEFAULT_DESKTOP_CONFIG: DesktopConfig = {
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

export interface HudMouseInteractionInput {
  captureProtection: boolean;
  clickThroughEnabled: boolean;
  controlsInteractive: boolean;
}

export function getHudMouseInteractionPolicy(input: HudMouseInteractionInput) {
  const available = input.captureProtection;
  const enabled = available && input.clickThroughEnabled;
  return {
    available,
    enabled,
    ignoreMouseEvents: enabled && !input.controlsInteractive,
  };
}
