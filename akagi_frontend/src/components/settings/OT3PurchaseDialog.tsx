import { ExternalLink, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ApiError } from '@/lib/api-client';
import {
  createOT3Order,
  createOT3Subscription,
  fetchOT3OrderResult,
  fetchOT3SubscriptionResult,
  type OT3Connection,
  redeemOT3Code,
} from '@/lib/ot3-api';
import { OT3_PRODUCTS, type OT3Product } from '@/lib/ot3-products';
import type { Settings } from '@/types';

type PurchasePhase =
  | 'idle'
  | 'creating'
  | 'approving'
  | 'redeeming'
  | 'redeem_failed'
  | 'done'
  | 'delivered'
  | 'failed';

type ActivePurchase = {
  kind: OT3Product['kind'];
  id: string;
  claim: string;
  connection: OT3Connection;
  renewKey: string;
};

const POLL_INITIAL_MS = 3000;
const POLL_MAX_MS = 30_000;
const POLL_DEADLINE_MS = 65 * 60_000;

interface OT3PurchaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings;
  updateSettingsBatch: (
    updates: { path: readonly string[]; value: unknown }[],
    shouldDebounce?: boolean,
  ) => void;
}

export function OT3PurchaseDialog({
  open,
  onOpenChange,
  settings,
  updateSettingsBatch,
}: OT3PurchaseDialogProps) {
  const { t } = useTranslation();
  const [productId, setProductId] = useState<OT3Product['id']>('pro-30');
  const [renew, setRenew] = useState(false);
  const [phase, setPhase] = useState<PurchasePhase>('idle');
  const [approveUrl, setApproveUrl] = useState('');
  const [error, setError] = useState('');
  const [resultText, setResultText] = useState('');
  const [recoverableCode, setRecoverableCode] = useState('');
  const activeRef = useRef<ActivePurchase | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const delayRef = useRef(POLL_INITIAL_MS);
  const startedAtRef = useRef(0);
  const generationRef = useRef(0);

  const selected = OT3_PRODUCTS.find((product) => product.id === productId) ?? OT3_PRODUCTS[0];
  const busy = phase === 'creating' || phase === 'approving' || phase === 'redeeming';

  const clearTimer = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  useEffect(() => () => clearTimer(), []);

  const saveKey = (key: string) => {
    updateSettingsBatch([
      { path: ['ot', 'api_key'], value: key },
      { path: ['ot', 'online'], value: true },
      { path: ['ot', 'protocol'], value: 'v3' },
    ]);
  };

  const finishWithKey = (key: string, plan?: string | null, until?: string | null) => {
    clearTimer();
    saveKey(key);
    setPhase('done');
    setResultText(
      t('settings.model_config.purchase_key_saved', {
        plan: plan || 'OT3',
        until: until || t('settings.model_config.unknown_expiry'),
      }),
    );
  };

  const schedulePoll = (generation: number) => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void poll(generation);
    }, delayRef.current);
  };

  const redeemPurchasedCode = async (
    generation: number,
    code: string,
    purchase: ActivePurchase,
    plan?: string | null,
  ) => {
    setPhase('redeeming');
    setRecoverableCode(code);
    try {
      const redeemed = await redeemOT3Code(purchase.connection, code, '', purchase.renewKey);
      if (generation !== generationRef.current) return;
      if (redeemed.key) {
        finishWithKey(redeemed.key, redeemed.plan || plan, redeemed.expires_at);
      } else {
        clearTimer();
        setPhase('done');
        setRecoverableCode('');
        setResultText(
          t('settings.model_config.purchase_key_extended', {
            last4: redeemed.key_last4,
            until: redeemed.expires_at,
          }),
        );
      }
    } catch (caught) {
      if (generation !== generationRef.current) return;
      setPhase('redeem_failed');
      setError(errorMessage(caught));
    }
  };

  const poll = async (generation: number) => {
    const purchase = activeRef.current;
    if (!purchase || generation !== generationRef.current || phase === 'failed') return;
    if (Date.now() - startedAtRef.current > POLL_DEADLINE_MS) {
      setPhase('failed');
      setError(t('settings.model_config.purchase_timeout'));
      return;
    }

    try {
      if (purchase.kind === 'onetime') {
        const result = await fetchOT3OrderResult(purchase.connection, purchase.id, purchase.claim);
        if (generation !== generationRef.current) return;
        if (result.status === 'pending') {
          delayRef.current = POLL_INITIAL_MS;
          schedulePoll(generation);
          return;
        }
        if (result.status === 'ready') {
          if (result.key) {
            finishWithKey(result.key, result.plan, null);
            return;
          }
          if (result.code) {
            await redeemPurchasedCode(generation, result.code, purchase, result.plan);
            return;
          }
          throw new Error(t('settings.model_config.purchase_missing_credential'));
        }
        if (result.status === 'delivered') {
          clearTimer();
          setPhase('delivered');
          return;
        }
        throw new Error(result.status);
      }

      const result = await fetchOT3SubscriptionResult(
        purchase.connection,
        purchase.id,
        purchase.claim,
      );
      if (generation !== generationRef.current) return;
      if (result.status === 'pending') {
        delayRef.current = POLL_INITIAL_MS;
        schedulePoll(generation);
        return;
      }
      if (result.status === 'ready' && result.key) {
        finishWithKey(result.key, result.plan, result.next_billing);
        return;
      }
      if (result.status === 'delivered') {
        clearTimer();
        setPhase('delivered');
        return;
      }
      throw new Error(result.status);
    } catch (caught) {
      if (generation !== generationRef.current) return;
      if (caught instanceof ApiError && caught.status === 404) {
        setPhase('failed');
        setError(t('settings.model_config.purchase_claim_failed'));
        return;
      }
      delayRef.current = Math.min(delayRef.current * 2, POLL_MAX_MS);
      setError(errorMessage(caught));
      schedulePoll(generation);
    }
  };

  const startPurchase = async () => {
    if (busy) return;
    clearTimer();
    generationRef.current += 1;
    const generation = generationRef.current;
    delayRef.current = POLL_INITIAL_MS;
    startedAtRef.current = Date.now();
    setError('');
    setResultText('');
    setRecoverableCode('');
    setApproveUrl('');
    setPhase('creating');

    const connection = {
      server: settings.ot.server,
      proxy: settings.ot.proxy_enabled ? settings.ot.proxy : '',
    };
    const renewKey = selected.kind === 'onetime' && renew ? settings.ot.api_key.trim() : '';
    try {
      if (selected.kind === 'onetime') {
        const created = await createOT3Order(connection, selected.id, !renewKey);
        if (generation !== generationRef.current) return;
        activeRef.current = {
          kind: selected.kind,
          id: created.order_id,
          claim: created.claim_secret,
          connection,
          renewKey,
        };
        setApproveUrl(created.approve_url);
        setPhase('approving');
        await window.electron.openExternal(created.approve_url);
      } else {
        const created = await createOT3Subscription(connection, selected.id);
        if (generation !== generationRef.current) return;
        activeRef.current = {
          kind: selected.kind,
          id: created.subscription_id,
          claim: created.claim_secret,
          connection,
          renewKey: '',
        };
        setApproveUrl(created.approve_url);
        setPhase('approving');
        await window.electron.openExternal(created.approve_url);
      }
      schedulePoll(generation);
    } catch (caught) {
      if (generation !== generationRef.current) return;
      setPhase('failed');
      setError(errorMessage(caught));
    }
  };

  const reset = () => {
    clearTimer();
    generationRef.current += 1;
    activeRef.current = null;
    setPhase('idle');
    setError('');
    setResultText('');
    setRecoverableCode('');
    setApproveUrl('');
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('settings.model_config.purchase_title')}</DialogTitle>
          <DialogDescription>{t('settings.model_config.purchase_desc')}</DialogDescription>
        </DialogHeader>

        {phase === 'idle' && (
          <div className='space-y-4'>
            <Select
              value={productId}
              onValueChange={(value) => setProductId(value as OT3Product['id'])}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OT3_PRODUCTS.map((product) => (
                  <SelectItem key={product.id} value={product.id}>
                    {t(product.labelKey)} · {product.displayPrice}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className='text-muted-foreground text-xs'>
              {t('settings.model_config.purchase_price_notice')}
            </p>
            {selected.kind === 'onetime' && settings.ot.api_key && (
              <label className='flex items-start gap-2 text-sm'>
                <Checkbox checked={renew} onCheckedChange={(value) => setRenew(value === true)} />
                <span>{t('settings.model_config.purchase_renew_current')}</span>
              </label>
            )}
          </div>
        )}

        {(phase === 'creating' || phase === 'approving' || phase === 'redeeming') && (
          <Alert variant='info'>
            <Loader2 className='animate-spin' />
            <AlertDescription>
              {phase === 'creating'
                ? t('settings.model_config.purchase_creating')
                : phase === 'redeeming'
                  ? t('settings.model_config.purchase_redeeming')
                  : t('settings.model_config.purchase_waiting')}
              {error && <p className='mt-1 text-xs'>{error}</p>}
            </AlertDescription>
          </Alert>
        )}

        {phase === 'redeem_failed' && (
          <Alert variant='error'>
            <AlertDescription>
              {t('settings.model_config.purchase_redeem_failed')}
              <code className='mt-2 block rounded bg-black/5 p-2 break-all select-all dark:bg-white/10'>
                {recoverableCode}
              </code>
              {error && <p className='mt-2'>{error}</p>}
            </AlertDescription>
          </Alert>
        )}

        {phase === 'done' && (
          <Alert variant='success'>
            <AlertDescription>{resultText}</AlertDescription>
          </Alert>
        )}

        {phase === 'delivered' && (
          <Alert variant='info'>
            <AlertDescription>{t('settings.model_config.purchase_delivered')}</AlertDescription>
          </Alert>
        )}

        {phase === 'failed' && (
          <Alert variant='error'>
            <AlertDescription>
              {t('settings.model_config.purchase_failed')}
              {error && <p className='mt-1 text-xs'>{error}</p>}
            </AlertDescription>
          </Alert>
        )}

        <DialogFooter>
          {approveUrl && phase === 'approving' && (
            <Button variant='outline' onClick={() => window.electron.openExternal(approveUrl)}>
              <ExternalLink className='h-4 w-4' />
              {t('settings.model_config.purchase_reopen')}
            </Button>
          )}
          {phase === 'idle' ? (
            <Button onClick={startPurchase} disabled={!settings.ot.server}>
              {t('settings.model_config.purchase_submit')}
            </Button>
          ) : !busy ? (
            <Button variant='outline' onClick={reset}>
              {t('settings.model_config.purchase_reset')}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}
