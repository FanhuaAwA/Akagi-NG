export type RendererRole = 'dashboard' | 'hud';

export const IPC_CHANNEL_ROLES = {
  'toggle-hud': ['dashboard', 'hud'],
  'desktop-reconcile': ['dashboard'],
  'hud-click-through-status': ['hud'],
  'hud-set-click-through': ['hud'],
  'hud-set-controls-interactive': ['hud'],
  'start-game': ['dashboard'],
  'request-shutdown': ['dashboard'],
  'mihomo-status': ['dashboard'],
  'mihomo-reconcile': ['dashboard'],
  'mihomo-stop': ['dashboard'],
  'update-liqi': ['dashboard'],
  'open-external': ['dashboard'],
  'minimize-window': ['dashboard'],
  'close-window': ['dashboard'],
  'maximize-window': ['dashboard'],
  'is-window-maximized': ['dashboard'],
  'check-resource-status': ['dashboard'],
  'get-startup-config': ['dashboard', 'hud'],
  'wait-for-backend': ['dashboard', 'hud'],
  'update-locale': ['dashboard'],
} as const satisfies Record<string, readonly RendererRole[]>;

export type IpcChannel = keyof typeof IPC_CHANNEL_ROLES;

const DEV_RENDERER_HOSTS = new Set(['localhost', '127.0.0.1']);
const MAJSOUL_HOST_SUFFIXES = ['maj-soul.com', 'mahjongsoul.com', 'yo-star.com'];

function hasExpectedRoute(url: URL, role: RendererRole): boolean {
  const route = url.hash || '#/';
  return role === 'hud' ? route === '#/hud' : route === '#/' || route === '#';
}

export function isTrustedRendererUrl(
  value: string,
  role: RendererRole,
  packaged: boolean,
): boolean {
  try {
    const url = new URL(value);
    if (url.username || url.password || !hasExpectedRoute(url, role)) return false;

    if (packaged) {
      return url.protocol === 'file:' && /\/dist\/renderer\/index\.html$/i.test(url.pathname);
    }

    return (
      url.protocol === 'http:' &&
      DEV_RENDERER_HOSTS.has(url.hostname) &&
      url.port === '5173' &&
      (url.pathname === '/' || url.pathname === '/index.html')
    );
  } catch {
    return false;
  }
}

export function isSafeExternalUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}

function hostMatches(hostname: string, suffix: string): boolean {
  return hostname === suffix || hostname.endsWith(`.${suffix}`);
}

export function isAllowedGameUrl(value: string, platform: string, initialUrl: string): boolean {
  try {
    const url = new URL(value);
    const initial = new URL(initialUrl);
    if (
      url.protocol !== 'https:' ||
      initial.protocol !== 'https:' ||
      url.username ||
      url.password ||
      initial.username ||
      initial.password
    ) {
      return false;
    }

    if (platform === 'majsoul' || platform === 'auto') {
      return MAJSOUL_HOST_SUFFIXES.some((suffix) => hostMatches(url.hostname, suffix));
    }
    if (platform === 'tenhou') {
      return hostMatches(url.hostname, 'tenhou.net');
    }
    return false;
  } catch {
    return false;
  }
}

export function isRoleAllowed(channel: IpcChannel, role: RendererRole): boolean {
  return (IPC_CHANNEL_ROLES[channel] as readonly RendererRole[]).includes(role);
}
