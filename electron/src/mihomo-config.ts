export interface MihomoConfigOptions {
  mitmHost: string;
  mitmPort: number;
  mixedPort: number;
  controllerPort: number;
  strictRoute: boolean;
  secret: string;
}

export function buildMihomoConfig(options: MihomoConfigOptions): Record<string, unknown> {
  return {
    mode: 'rule',
    'log-level': 'info',
    ipv6: false,
    'allow-lan': false,
    'mixed-port': options.mixedPort,
    'external-controller': `127.0.0.1:${options.controllerPort}`,
    secret: options.secret,
    'find-process-mode': 'strict',
    tun: {
      enable: true,
      stack: 'mixed',
      'auto-route': true,
      'auto-detect-interface': true,
      'strict-route': options.strictRoute,
    },
    sniffer: {
      enable: true,
      sniff: {
        TLS: { ports: [443, 8443] },
        HTTP: { ports: [80, '8080-8880'], 'override-destination': true },
      },
    },
    proxies: [
      {
        name: 'Akagi-Mitm',
        type: 'http',
        server: options.mitmHost,
        port: options.mitmPort,
      },
    ],
    rules: [
      'PROCESS-NAME,mihomo.exe,DIRECT',
      'PROCESS-NAME,Akagi-NG.exe,DIRECT',
      'PROCESS-NAME,akagi-ng.exe,DIRECT',
      'PROCESS-NAME,electron.exe,DIRECT',
      'PROCESS-NAME,python.exe,DIRECT',
      'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
      'PROCESS-NAME,Jantama_MahjongSoul.exe,Akagi-Mitm',
      'PROCESS-NAME,MahjongSoul.exe,Akagi-Mitm',
      'PROCESS-NAME,Mahjong-JP.exe,Akagi-Mitm',
      'PROCESS-NAME,mahjongp.exe,Akagi-Mitm',
      'DOMAIN-KEYWORD,maj-soul,Akagi-Mitm',
      'DOMAIN-KEYWORD,majsoul,Akagi-Mitm',
      'DOMAIN-KEYWORD,mahjongsoul,Akagi-Mitm',
      'DOMAIN-KEYWORD,tenhou,Akagi-Mitm',
      'MATCH,DIRECT',
    ],
  };
}
