import { fetchJson } from '@/lib/api-client';
import type {
  OT3CreatedOrder,
  OT3CreatedSubscription,
  OT3Health,
  OT3KeyStatus,
  OT3ModelInfo,
  OT3OrderResult,
  OT3RedeemResponse,
  OT3SubscriptionResult,
} from '@/types';

export type OT3Connection = {
  server: string;
  api_key?: string;
  proxy?: string;
};

async function post<T>(path: string, body: object): Promise<T> {
  return fetchJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function checkOT3Health(connection: OT3Connection): Promise<OT3Health> {
  return post('/api/ot3/health', connection);
}

export function fetchOT3KeyStatus(connection: OT3Connection): Promise<OT3KeyStatus> {
  return post('/api/ot3/key-status', connection);
}

export function fetchOT3Models(connection: OT3Connection): Promise<OT3ModelInfo[]> {
  return post('/api/ot3/models', connection);
}

export function redeemOT3Code(
  connection: OT3Connection,
  code: string,
  email = '',
  renewKey = '',
): Promise<OT3RedeemResponse> {
  return post('/api/ot3/redeem', {
    ...connection,
    code,
    email,
    renew_key: renewKey,
  });
}

export function createOT3Order(
  connection: OT3Connection,
  product: string,
  redeem: boolean,
): Promise<OT3CreatedOrder> {
  return post('/api/ot3/purchase/order', { ...connection, product, redeem });
}

export function fetchOT3OrderResult(
  connection: OT3Connection,
  orderId: string,
  claim: string,
): Promise<OT3OrderResult> {
  return post('/api/ot3/purchase/order/result', {
    ...connection,
    order_id: orderId,
    claim,
  });
}

export function createOT3Subscription(
  connection: OT3Connection,
  product: string,
): Promise<OT3CreatedSubscription> {
  return post('/api/ot3/purchase/subscription', { ...connection, product });
}

export function fetchOT3SubscriptionResult(
  connection: OT3Connection,
  subscriptionId: string,
  claim: string,
): Promise<OT3SubscriptionResult> {
  return post('/api/ot3/purchase/subscription/result', {
    ...connection,
    subscription_id: subscriptionId,
    claim,
  });
}
