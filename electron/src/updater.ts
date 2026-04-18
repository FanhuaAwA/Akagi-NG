import { app, net } from 'electron';

import { GITHUB_RELEASES_API } from './constants.js';
import { safeSend } from './utils.js';
import type { WindowManager } from './window-manager.js';

interface GitHubRelease {
  tag_name: string;
}

export class UpdaterManager {
  constructor(private windowManager: WindowManager) {}

  public checkForUpdates() {
    if (!app.isPackaged) {
      console.log('[Updater] Running in dev mode, skipping update check.');
      return;
    }

    this.fetchLatestVersion().catch((err) => {
      console.error('[Updater] Error checking for updates:', err);
    });
  }

  private async fetchLatestVersion(): Promise<void> {
    const response = await net.fetch(GITHUB_RELEASES_API, {
      headers: { Accept: 'application/vnd.github.v3+json' },
    });

    if (!response.ok) {
      console.warn(`[Updater] GitHub API returned ${response.status}`);
      return;
    }

    const data = (await response.json()) as GitHubRelease;
    const latestVersion = data.tag_name.replace(/^v/, '');
    const currentVersion = app.getVersion();

    if (latestVersion !== currentVersion) {
      console.log(`[Updater] Update available: v${currentVersion} → v${latestVersion}`);
      this.notifyWindow('app:update-available', latestVersion);
    } else {
      console.log(`[Updater] Already on latest version: v${currentVersion}`);
    }
  }

  private notifyWindow(channel: string, data?: unknown) {
    const mainWin = this.windowManager.getMainWindow();
    safeSend(mainWin, channel, data);
  }
}
