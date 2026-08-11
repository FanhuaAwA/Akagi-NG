import assert from 'node:assert/strict';

import { buildMihomoConfig } from '../electron/src/mihomo-config.js';

function main(): void {
  const config = buildMihomoConfig({
    mitmHost: '127.0.0.1',
    mitmPort: 6789,
    mixedPort: 7890,
    controllerPort: 9090,
    strictRoute: false,
    secret: 'test-secret',
  });
  const rules = config.rules as string[];
  const upstreamBypass = rules.indexOf('PROCESS-NAME,verge-mihomo.exe,DIRECT');
  const majsoulDomain = rules.indexOf('DOMAIN-KEYWORD,majsoul,Akagi-Mitm');

  assert.ok(upstreamBypass >= 0, 'Clash Verge upstream core must bypass Akagi TUN.');
  assert.ok(
    upstreamBypass < majsoulDomain,
    'The upstream process bypass must run before Majsoul domain interception.',
  );
  assert.equal(rules.at(-1), 'MATCH,DIRECT');

  console.log('✅ Mihomo proxy-loop regression tests passed.');
}

main();
