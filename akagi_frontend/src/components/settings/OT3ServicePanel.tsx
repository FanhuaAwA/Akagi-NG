import {
  Activity,
  CheckCircle2,
  KeyRound,
  Loader2,
  MessageCircle,
  RefreshCw,
  ShoppingCart,
  Ticket,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';

import { OT3PurchaseDialog } from '@/components/settings/OT3PurchaseDialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
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
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { checkFlyAHealth, fetchFlyAModels, fetchFlyAQuota } from '@/lib/flya-api';
import { checkOT3Health, fetchOT3KeyStatus, fetchOT3Models, redeemOT3Code } from '@/lib/ot3-api';
import type { FlyAQuota, OT3Health, OT3KeyStatus, OT3ModelInfo, Settings } from '@/types';

const FLYA_DISCORD_URL = 'https://discord.com/invite/YEgQRT4MMU';
const FLYA_QQ_GROUP = '1093245435';

function formatQuotaTime(value: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function quotaNeedsWarning(quota: FlyAQuota) {
  if (quota.status === 'grace') return true;
  return quota.key_kind === 'paygo'
    ? quota.remaining.startsWith('-')
    : quota.five_hour.remaining.startsWith('-') || quota.weekly.remaining.startsWith('-');
}

function QuotaProgress({
  label,
  used,
  limit,
  remaining,
  resets,
}: {
  label: string;
  used: string;
  limit: string;
  remaining: string;
  resets?: string;
}) {
  const percent = Math.min(Math.max((Number(used) / Number(limit)) * 100 || 0, 0), 100);
  const lowQuota = Number(used) / Number(limit) > 0.95;
  const overdrawn = remaining.startsWith('-');

  return (
    <div className='bg-background/60 w-full space-y-2 rounded-md border p-3'>
      <div className='flex items-center justify-between gap-3 text-sm'>
        <span className='font-medium'>{label}</span>
        <span className='font-mono tabular-nums'>
          {used} / {limit}
        </span>
      </div>
      <div
        role='progressbar'
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${used} / ${limit}`}
        className='bg-muted h-2.5 overflow-hidden rounded-full'
      >
        <div
          className={`h-full rounded-full ${lowQuota ? 'bg-red-500' : 'bg-emerald-500'}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className='text-muted-foreground flex flex-wrap justify-between gap-x-3 text-xs'>
        <span className={overdrawn ? 'font-medium text-amber-600 dark:text-amber-300' : undefined}>
          {remaining}
        </span>
        {resets && <span>{resets}</span>}
      </div>
    </div>
  );
}

interface OT3ServicePanelProps {
  settings: Settings;
  updateSettingsBatch: (
    updates: { path: readonly string[]; value: unknown }[],
    shouldDebounce?: boolean,
  ) => void;
}

export function OT3ServicePanel({ settings, updateSettingsBatch }: OT3ServicePanelProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<'health' | 'key' | 'models' | ''>('');
  const [health, setHealth] = useState<OT3Health | null>(null);
  const [keyStatus, setKeyStatus] = useState<OT3KeyStatus | null>(null);
  const [quota, setQuota] = useState<FlyAQuota | null>(null);
  const [models, setModels] = useState<OT3ModelInfo[]>([]);
  const [error, setError] = useState('');
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [getKeyOpen, setGetKeyOpen] = useState(false);
  const fetchedConnectionRef = useRef('');
  const isFlyA = settings.ot.provider === 'flya_test_api';
  const hasKey = isFlyA ? settings.ot.flya_api_key_configured : Boolean(settings.ot.api_key);

  const connection = useMemo(
    () => ({
      server: isFlyA ? settings.ot.flya_server : settings.ot.server,
      api_key: isFlyA ? undefined : settings.ot.api_key,
      proxy: settings.ot.proxy_enabled ? settings.ot.proxy : '',
    }),
    [
      isFlyA,
      settings.ot.api_key,
      settings.ot.flya_server,
      settings.ot.proxy,
      settings.ot.proxy_enabled,
      settings.ot.server,
    ],
  );
  const fourPlayerModels = models.filter((model) => !isThreePlayerModel(model));
  const threePlayerModels = models.filter(isThreePlayerModel);

  useEffect(() => {
    const signature = `${settings.ot.provider}\n${connection.server}\n${hasKey}\n${connection.proxy}`;
    if (
      !connection.server ||
      !hasKey ||
      (isFlyA && settings.ot.flya_api_key) ||
      fetchedConnectionRef.current === signature
    ) {
      return;
    }
    fetchedConnectionRef.current = signature;
    const request = isFlyA ? fetchFlyAModels(connection) : fetchOT3Models(connection);
    request
      .then((fetchedModels) => {
        setModels(fetchedModels);
        const updates: { path: readonly string[]; value: unknown }[] = [];
        const firstFourPlayer = fetchedModels.find((model) => !isThreePlayerModel(model));
        const firstThreePlayer = fetchedModels.find(isThreePlayerModel);
        if (!isFlyA && !settings.ot.model_4p && firstFourPlayer) {
          updates.push({
            path: ['ot', 'model_4p'],
            value: firstFourPlayer.id,
          });
        }
        if (!isFlyA && !settings.ot.model_3p && firstThreePlayer) {
          updates.push({
            path: ['ot', 'model_3p'],
            value: firstThreePlayer.id,
          });
        }
        if (updates.length > 0) {
          updateSettingsBatch(updates);
        }
      })
      .catch((caught: unknown) => {
        fetchedConnectionRef.current = '';
        setError(caught instanceof Error ? caught.message : String(caught));
      });
  }, [
    connection,
    hasKey,
    isFlyA,
    settings.ot.api_key,
    settings.ot.flya_api_key_configured,
    settings.ot.flya_api_key,
    settings.ot.flya_model_3p,
    settings.ot.flya_model_4p,
    settings.ot.flya_server,
    settings.ot.model_3p,
    settings.ot.model_4p,
    settings.ot.server,
    settings.ot.provider,
    updateSettingsBatch,
  ]);

  const run = async (kind: 'health' | 'key' | 'models') => {
    setBusy(kind);
    setError('');
    try {
      if (kind === 'health') {
        setHealth(await (isFlyA ? checkFlyAHealth(connection) : checkOT3Health(connection)));
      } else if (kind === 'key') {
        if (isFlyA) {
          setQuota(await fetchFlyAQuota(connection));
        } else {
          setKeyStatus(await fetchOT3KeyStatus(connection));
        }
      } else {
        setModels(await (isFlyA ? fetchFlyAModels(connection) : fetchOT3Models(connection)));
      }
    } catch (caught) {
      if (kind === 'key' && isFlyA) setQuota(null);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy('');
    }
  };

  const updateModel = (game: '4p' | '3p', value: string) => {
    updateSettingsBatch([
      {
        path: [
          'ot',
          isFlyA
            ? game === '4p'
              ? 'flya_model_4p'
              : 'flya_model_3p'
            : game === '4p'
              ? 'model_4p'
              : 'model_3p',
        ],
        value: value === '__default' ? '' : value,
      },
    ]);
  };

  return (
    <div className='border-border space-y-3 rounded-lg border p-4'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div>
          <p className='text-sm font-medium'>
            {t(
              isFlyA
                ? 'settings.model_config.flya_account_tools'
                : 'settings.model_config.ot3_account_tools',
            )}
          </p>
          <p className='text-muted-foreground text-xs'>
            {t(
              isFlyA
                ? 'settings.model_config.flya_account_tools_desc'
                : 'settings.model_config.ot3_account_tools_desc',
            )}
          </p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('health')}
            disabled={Boolean(busy) || !connection.server || !hasKey}
          >
            {busy === 'health' ? <Loader2 className='animate-spin' /> : <Activity />}
            {t('settings.model_config.health_check')}
          </Button>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('key')}
            disabled={Boolean(busy) || !connection.server || !hasKey}
          >
            {busy === 'key' ? <Loader2 className='animate-spin' /> : <KeyRound />}
            {t(isFlyA ? 'settings.model_config.query_quota' : 'settings.model_config.query_key')}
          </Button>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('models')}
            disabled={Boolean(busy) || !connection.server || !hasKey}
          >
            {busy === 'models' ? <Loader2 className='animate-spin' /> : <RefreshCw />}
            {t('settings.model_config.fetch_models')}
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant='error'>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {health && (
        <Alert variant={health.status.toLowerCase() === 'ok' ? 'success' : 'warning'}>
          <CheckCircle2 />
          <AlertDescription>
            {t('settings.model_config.health_result', {
              status: health.status,
              count: health.models.length,
            })}
            {Object.keys(health.queue_depth).length > 0 && (
              <div className='mt-2 flex flex-wrap gap-1'>
                {Object.entries(health.queue_depth).map(([model, depth]) => (
                  <Badge key={model} variant='outline'>
                    {model}: {depth}
                  </Badge>
                ))}
              </div>
            )}
          </AlertDescription>
        </Alert>
      )}

      {keyStatus && (
        <Alert variant='info'>
          <KeyRound />
          <AlertDescription>
            {t('settings.model_config.key_result', {
              plan: keyStatus.plan,
              expires: keyStatus.expires_at,
              usage: keyStatus.usage_today,
              rpd: keyStatus.rpd,
              rpm: keyStatus.rpm,
              topk: keyStatus.topk,
            })}
          </AlertDescription>
        </Alert>
      )}

      {quota && isFlyA && (
        <Alert variant={quotaNeedsWarning(quota) ? 'warning' : 'info'}>
          <KeyRound />
          <AlertDescription>
            <p>
              {t('settings.model_config.quota_summary', {
                kind: t(`settings.model_config.quota_kind_${quota.key_kind}`),
                status: t(`settings.model_config.quota_status_${quota.status}`),
              })}
            </p>
            {quota.key_kind === 'paygo' ? (
              <>
                <QuotaProgress
                  label={t('settings.model_config.quota_kind_paygo')}
                  used={quota.used}
                  limit={quota.total}
                  remaining={t('settings.model_config.quota_remaining', {
                    remaining: quota.remaining,
                  })}
                />
                <p>
                  {t('settings.model_config.quota_paygo_expires', {
                    expires: formatQuotaTime(quota.expires_at),
                  })}
                </p>
              </>
            ) : (
              <>
                <QuotaProgress
                  label={t('settings.model_config.quota_five_hour')}
                  used={quota.five_hour.used}
                  limit={quota.five_hour.limit}
                  remaining={t('settings.model_config.quota_remaining', {
                    remaining: quota.five_hour.remaining,
                  })}
                  resets={t('settings.model_config.quota_resets', {
                    resets: formatQuotaTime(quota.five_hour.resets_at),
                  })}
                />
                <QuotaProgress
                  label={t('settings.model_config.quota_weekly')}
                  used={quota.weekly.used}
                  limit={quota.weekly.limit}
                  remaining={t('settings.model_config.quota_remaining', {
                    remaining: quota.weekly.remaining,
                  })}
                  resets={t('settings.model_config.quota_resets', {
                    resets: formatQuotaTime(quota.weekly.resets_at),
                  })}
                />
                <p>
                  {t('settings.model_config.quota_expires', {
                    expires: formatQuotaTime(quota.expires_at),
                  })}
                </p>
              </>
            )}
            {quota.status === 'grace' && quota.destroy_at && (
              <p>
                {t('settings.model_config.quota_destroy_at', {
                  destroy: formatQuotaTime(quota.destroy_at),
                })}
              </p>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className='grid grid-cols-2 gap-3'>
        <div>
          <p className='mb-1 text-xs font-medium'>
            {t(
              isFlyA ? 'settings.model_config.flya_model_4p' : 'settings.model_config.ot3_model_4p',
            )}
          </p>
          <Select
            value={(isFlyA ? settings.ot.flya_model_4p : settings.ot.model_4p) || '__default'}
            onValueChange={(value) => updateModel('4p', value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__default'>{t('settings.model_config.server_default')}</SelectItem>
              {fourPlayerModels.map((model) => (
                <SelectItem key={model.id} value={model.id} disabled={model.available === false}>
                  {model.desc || model.id}
                  {isFlyA && model.multiplier !== undefined
                    ? ` - ${Number(model.multiplier)}x`
                    : ''}
                  {!isFlyA && model.desc && model.desc !== model.id ? ` · ${model.id}` : ''}
                  {!isFlyA && model.cost_milliunits !== undefined
                    ? ` · ${model.cost_milliunits / 1000}`
                    : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <p className='mb-1 text-xs font-medium'>
            {t(
              isFlyA ? 'settings.model_config.flya_model_3p' : 'settings.model_config.ot3_model_3p',
            )}
          </p>
          <Select
            value={(isFlyA ? settings.ot.flya_model_3p : settings.ot.model_3p) || '__default'}
            onValueChange={(value) => updateModel('3p', value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__default'>{t('settings.model_config.server_default')}</SelectItem>
              {threePlayerModels.map((model) => (
                <SelectItem key={model.id} value={model.id} disabled={model.available === false}>
                  {model.desc || model.id}
                  {isFlyA && model.multiplier !== undefined
                    ? ` - ${Number(model.multiplier)}x`
                    : ''}
                  {!isFlyA && model.desc && model.desc !== model.id ? ` · ${model.id}` : ''}
                  {!isFlyA && model.cost_milliunits !== undefined
                    ? ` · ${model.cost_milliunits / 1000}`
                    : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {isFlyA ? (
        <Button size='sm' onClick={() => setGetKeyOpen(true)}>
          <MessageCircle />
          {t('settings.model_config.get_key')}
        </Button>
      ) : (
        <div className='flex flex-wrap gap-2'>
          <Button size='sm' variant='outline' onClick={() => setRedeemOpen(true)}>
            <Ticket />
            {t('settings.model_config.redeem_code')}
          </Button>
          <Button size='sm' onClick={() => setPurchaseOpen(true)} disabled={!settings.ot.server}>
            <ShoppingCart />
            {t('settings.model_config.purchase_key')}
          </Button>
        </div>
      )}

      <RedeemDialog
        open={redeemOpen}
        onOpenChange={setRedeemOpen}
        settings={settings}
        updateSettingsBatch={updateSettingsBatch}
        onSuccess={(message) => toast.success(message)}
      />
      <OT3PurchaseDialog
        open={purchaseOpen}
        onOpenChange={setPurchaseOpen}
        settings={settings}
        updateSettingsBatch={updateSettingsBatch}
      />
      <Dialog open={getKeyOpen} onOpenChange={setGetKeyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('settings.model_config.get_key')}</DialogTitle>
            <DialogDescription>{t('settings.model_config.get_key_desc')}</DialogDescription>
          </DialogHeader>
          <div className='space-y-3 text-sm'>
            <Button
              className='w-full'
              variant='outline'
              onClick={() => void window.electron.openExternal(FLYA_DISCORD_URL)}
            >
              Discord · {FLYA_DISCORD_URL}
            </Button>
            <div className='border-border rounded-lg border px-4 py-3'>
              {t('settings.model_config.qq_group')}：{FLYA_QQ_GROUP}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface RedeemDialogProps extends OT3ServicePanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess: (message: string) => void;
}

function RedeemDialog({
  open,
  onOpenChange,
  settings,
  updateSettingsBatch,
  onSuccess,
}: RedeemDialogProps) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [email, setEmail] = useState('');
  const [renew, setRenew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setBusy(true);
    setError('');
    try {
      const result = await redeemOT3Code(
        {
          server: settings.ot.server,
          proxy: settings.ot.proxy_enabled ? settings.ot.proxy : '',
        },
        code,
        email,
        renew ? settings.ot.api_key : '',
      );
      if (result.key) {
        updateSettingsBatch([
          { path: ['ot', 'api_key'], value: result.key },
          { path: ['ot', 'online'], value: true },
          { path: ['ot', 'protocol'], value: 'v3' },
        ]);
        onSuccess(
          t('settings.model_config.redeem_new_key', {
            plan: result.plan,
            last4: result.key_last4,
          }),
        );
      } else {
        onSuccess(
          t('settings.model_config.redeem_extended', {
            last4: result.key_last4,
            expires: result.expires_at,
          }),
        );
      }
      setCode('');
      onOpenChange(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('settings.model_config.redeem_title')}</DialogTitle>
          <DialogDescription>{t('settings.model_config.redeem_desc')}</DialogDescription>
        </DialogHeader>
        <div className='space-y-3'>
          <Input
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder={t('settings.model_config.redeem_code')}
            autoComplete='off'
          />
          {!renew && (
            <Input
              type='email'
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder={t('settings.model_config.redeem_email')}
              autoComplete='email'
            />
          )}
          {settings.ot.api_key && (
            <label className='flex items-start gap-2 text-sm'>
              <Checkbox checked={renew} onCheckedChange={(value) => setRenew(value === true)} />
              <span>{t('settings.model_config.redeem_renew')}</span>
            </label>
          )}
          {error && (
            <Alert variant='error'>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>
        <DialogFooter>
          <Button
            onClick={() => void submit()}
            disabled={busy || !code.trim() || !settings.ot.server}
          >
            {busy && <Loader2 className='animate-spin' />}
            {t('settings.model_config.redeem_submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function isThreePlayerModel(model: OT3ModelInfo): boolean {
  const game = model.game.trim().toLowerCase();
  const id = model.id.trim().toLowerCase();
  return game === '3p' || game === 'sanma' || game.includes('3-player') || id.startsWith('3p-');
}
