import type { MajsoulServer, Platform } from '@/config/platforms';

export interface SimCandidate {
  tile: string;
  confidence: number;
}

export interface Recommendation {
  action: string;
  confidence?: number;
  consumed?: string[];
  sim_candidates?: SimCandidate[];
  tile?: string;
}

export type EngineType = 'mortal' | 'akagiot' | 'flya' | 'unknown' | 'null';
export type DecisionSource =
  | 'local'
  | 'ot3'
  | 'ot3_fallback'
  | 'flya'
  | 'flya_fallback'
  | 'legacy_ot';

export interface FullRecommendationData {
  recommendations: Recommendation[];
  engine_type: EngineType;
  fallback_used: boolean;
  circuit_open: boolean;
  decision_source: DecisionSource;
  flya_model?: string;
}

export interface NotificationItem {
  level?: string;
  code: string;
  msg?: string;
}

export interface ApiResponse<T = void> {
  ok: boolean;
  data?: T;
  error?: string;
}

export interface Settings {
  log_level: string;
  locale: string;
  game_url: string;
  majsoul_server: MajsoulServer;
  platform: Platform;
  mitm: {
    enabled: boolean;
    host: string;
    port: number;
    upstream: string;
  };
  mihomo: {
    enabled: boolean;
    mixed_port: number;
    controller_port: number;
    strict_route: boolean;
  };
  desktop: {
    overlay_mode: 'standard' | 'advanced';
    advanced_host: 'auto' | 'discord' | 'protected';
    capture_protection: boolean;
    privacy_mode: boolean;
    tray_visible: boolean;
    start_hidden: boolean;
    restore_shortcut: string;
  };
  server: {
    host: string;
    port: number;
  };
  ot: {
    online: boolean;
    provider: 'akagi_ot3' | 'flya_test_api';
    server: string;
    api_key: string;
    protocol: 'v3' | 'legacy';
    model_4p: string;
    model_3p: string;
    flya_server: string;
    flya_api_key: string;
    flya_api_key_configured: boolean;
    flya_api_key_last4: string;
    flya_model_4p: string;
    flya_model_3p: string;
    proxy_enabled: boolean;
    proxy: string;
  };
  model_config: {
    model_4p: string;
    model_3p: string;
    temperature: number;
  };
  autoplay: {
    enabled: boolean;
    window_keyword: string;
    timing: {
      first_tile: number;
      rand_min: number;
      rand_max: number;
      candidate: number;
    };
    input: {
      bezier_smoothing: number;
      bezier_steps: number;
    };
  };
}

export interface SaveSettingsResponse extends ApiResponse<Settings> {
  restartRequired?: boolean;
  proxyChanged?: boolean;
  proxyError?: string;
  desktopChanged?: boolean;
}

export interface OT3Health {
  status: string;
  models: string[];
  queue_depth: Record<string, number>;
}

export interface OT3KeyStatus {
  plan: string;
  expires_at: string;
  usage_today: number;
  rpd: number;
  rpm: number;
  topk: number;
}

export interface OT3ModelInfo {
  id: string;
  game: string;
  desc: string;
  display_name?: string;
  available?: boolean;
  unavailable_reason?: string | null;
  cost_milliunits?: number;
  multiplier?: string;
  provider?: string;
  rule_line?: string;
}

interface FlyAQuotaBase {
  status: 'active' | 'grace';
  expires_at: string;
  destroy_at?: string | null;
}

interface FlyAQuotaWindow {
  limit: string;
  used: string;
  remaining: string;
  resets_at: string;
}

export interface FlyAPaygoQuota extends FlyAQuotaBase {
  key_kind: 'paygo';
  total: string;
  used: string;
  remaining: string;
}

export interface FlyASubscriptionQuota extends FlyAQuotaBase {
  key_kind: 'subscription';
  five_hour: FlyAQuotaWindow;
  weekly: FlyAQuotaWindow;
}

export type FlyAQuota = FlyAPaygoQuota | FlyASubscriptionQuota;

export interface OT3RedeemResponse {
  key?: string | null;
  key_last4: string;
  plan: string;
  expires_at: string;
  extended: boolean;
}

export interface OT3CreatedOrder {
  order_id: string;
  approve_url: string;
  claim_secret: string;
}

export interface OT3CreatedSubscription {
  subscription_id: string;
  approve_url: string;
  claim_secret: string;
}

export interface OT3OrderResult {
  status: string;
  code?: string | null;
  key?: string | null;
  plan?: string | null;
  days?: number | null;
}

export interface OT3SubscriptionResult {
  status: string;
  key?: string | null;
  plan?: string | null;
  next_billing?: string | null;
}

type Primitive = string | number | boolean | null | undefined | symbol | bigint;

export type Paths<T> = {
  [K in keyof T]: T[K] extends Primitive
    ? [K]
    : T[K] extends object
      ? [K] | [K, ...Paths<T[K]>]
      : [K];
}[keyof T];

export type PathValue<T, P extends readonly unknown[]> = P extends [infer K]
  ? K extends keyof T
    ? T[K]
    : never
  : P extends [infer K, ...infer R]
    ? K extends keyof T
      ? PathValue<T[K], R>
      : never
    : never;

export type Theme = 'light' | 'dark' | 'system';

export type SSEErrorCode = 'config_error' | 'service_disconnected';

export interface ResourceStatus {
  lib: boolean;
  models: boolean;
  missingCritical: string[];
  missingOptional: string[];
}
