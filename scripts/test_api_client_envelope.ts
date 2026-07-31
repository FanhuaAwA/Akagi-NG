import assert from 'node:assert/strict';

import { fetchJson } from '../akagi_frontend/src/lib/api-client';
import {
  DEFAULT_DESKTOP_CONFIG,
  getDashboardWindowPolicy,
  getHudMouseInteractionPolicy,
} from '../electron/src/desktop-config';

const payload = {
  ok: true,
  data: { locale: 'zh-CN' },
  desktopChanged: true,
  proxyChanged: false,
};

async function main() {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });

  try {
    const data = await fetchJson<{ locale: string }>('/api/settings');
    assert.equal(data.locale, 'zh-CN');

    const envelope = await fetchJson<typeof payload>('/api/settings', {}, 'envelope');
    assert.equal(envelope.desktopChanged, true);
    assert.equal(envelope.proxyChanged, false);
    assert.equal(envelope.data.locale, 'zh-CN');

    const trayPolicy = getDashboardWindowPolicy(DEFAULT_DESKTOP_CONFIG);
    assert.equal(trayPolicy.startVisible, true);
    assert.equal(trayPolicy.skipTaskbar, false);
    assert.equal(trayPolicy.contentProtection, true);
    assert.equal(trayPolicy.closeAction, 'hide');

    const noTrayPolicy = getDashboardWindowPolicy({
      ...DEFAULT_DESKTOP_CONFIG,
      trayVisible: false,
    });
    assert.equal(noTrayPolicy.startVisible, true);
    assert.equal(noTrayPolicy.skipTaskbar, false);
    assert.equal(noTrayPolicy.contentProtection, true);
    assert.equal(noTrayPolicy.closeAction, 'minimize');

    const unprotectedPolicy = getDashboardWindowPolicy({
      ...DEFAULT_DESKTOP_CONFIG,
      captureProtection: false,
    });
    assert.equal(unprotectedPolicy.startVisible, true);
    assert.equal(unprotectedPolicy.skipTaskbar, false);
    assert.equal(unprotectedPolicy.contentProtection, false);

    const clickThrough = getHudMouseInteractionPolicy({
      clickThroughEnabled: true,
      controlsInteractive: false,
    });
    assert.equal(clickThrough.enabled, true);
    assert.equal(clickThrough.ignoreMouseEvents, true);

    const interactiveControls = getHudMouseInteractionPolicy({
      clickThroughEnabled: true,
      controlsInteractive: true,
    });
    assert.equal(interactiveControls.enabled, true);
    assert.equal(interactiveControls.ignoreMouseEvents, false);

    const clickThroughDisabled = getHudMouseInteractionPolicy({
      clickThroughEnabled: false,
      controlsInteractive: false,
    });
    assert.equal(clickThroughDisabled.enabled, false);
    assert.equal(clickThroughDisabled.ignoreMouseEvents, false);
    console.log('API response envelope regression test passed.');
  } finally {
    globalThis.fetch = originalFetch;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
