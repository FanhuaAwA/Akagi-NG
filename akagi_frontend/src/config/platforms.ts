/**
 * 平台常量和配置
 */

export const PLATFORMS = {
  MAJSOUL: 'majsoul',
  TENHOU: 'tenhou',
  RIICHI_CITY: 'riichi_city',
  AMATSUKI: 'amatsuki',
  AUTO: 'auto',
} as const;

export const MAJSOUL_SERVERS = {
  CN: 'cn',
  JP: 'jp',
  EN: 'en',
} as const;

export type Platform = (typeof PLATFORMS)[keyof typeof PLATFORMS];
export type MajsoulServer = (typeof MAJSOUL_SERVERS)[keyof typeof MAJSOUL_SERVERS];

export const MITM_REQUIRED_PLATFORMS: Platform[] = [PLATFORMS.RIICHI_CITY, PLATFORMS.AMATSUKI];
