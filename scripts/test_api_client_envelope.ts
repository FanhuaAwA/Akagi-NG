import assert from 'node:assert/strict';

import { fetchJson } from '../akagi_frontend/src/lib/api-client';

const payload = {
  ok: true,
  data: { locale: 'zh-CN' },
  desktopChanged: true,
  proxyChanged: true,
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
    assert.equal(envelope.proxyChanged, true);
    assert.equal(envelope.data.locale, 'zh-CN');
    console.log('API response envelope regression test passed.');
  } finally {
    globalThis.fetch = originalFetch;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
