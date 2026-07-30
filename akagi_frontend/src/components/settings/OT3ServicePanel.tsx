import {
  Activity,
  CheckCircle2,
  KeyRound,
  Loader2,
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
import { checkOT3Health, fetchOT3KeyStatus, fetchOT3Models, redeemOT3Code } from '@/lib/ot3-api';
import type { OT3Health, OT3KeyStatus, OT3ModelInfo, Settings } from '@/types';

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
  const [models, setModels] = useState<OT3ModelInfo[]>([]);
  const [error, setError] = useState('');
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const fetchedConnectionRef = useRef('');

  const connection = useMemo(
    () => ({
      server: settings.ot.server,
      api_key: settings.ot.api_key,
      proxy: settings.ot.proxy_enabled ? settings.ot.proxy : '',
    }),
    [settings.ot.api_key, settings.ot.proxy, settings.ot.proxy_enabled, settings.ot.server],
  );
  const fourPlayerModels = models.filter((model) => !isThreePlayerModel(model));
  const threePlayerModels = models.filter(isThreePlayerModel);

  useEffect(() => {
    const signature = `${connection.server}\n${connection.api_key}\n${connection.proxy}`;
    if (!settings.ot.server || !settings.ot.api_key || fetchedConnectionRef.current === signature) {
      return;
    }
    fetchedConnectionRef.current = signature;
    fetchOT3Models(connection)
      .then((fetchedModels) => {
        setModels(fetchedModels);
        const updates: { path: readonly string[]; value: unknown }[] = [];
        const firstFourPlayer = fetchedModels.find((model) => !isThreePlayerModel(model));
        const firstThreePlayer = fetchedModels.find(isThreePlayerModel);
        if (!settings.ot.model_4p && firstFourPlayer) {
          updates.push({ path: ['ot', 'model_4p'], value: firstFourPlayer.id });
        }
        if (!settings.ot.model_3p && firstThreePlayer) {
          updates.push({ path: ['ot', 'model_3p'], value: firstThreePlayer.id });
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
    settings.ot.api_key,
    settings.ot.model_3p,
    settings.ot.model_4p,
    settings.ot.server,
    updateSettingsBatch,
  ]);

  const run = async (kind: 'health' | 'key' | 'models') => {
    setBusy(kind);
    setError('');
    try {
      if (kind === 'health') {
        setHealth(await checkOT3Health(connection));
      } else if (kind === 'key') {
        setKeyStatus(await fetchOT3KeyStatus(connection));
      } else {
        setModels(await fetchOT3Models(connection));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy('');
    }
  };

  const updateModel = (game: '4p' | '3p', value: string) => {
    updateSettingsBatch([
      {
        path: ['ot', game === '4p' ? 'model_4p' : 'model_3p'],
        value: value === '__default' ? '' : value,
      },
    ]);
  };

  return (
    <div className='border-border space-y-3 rounded-lg border p-4'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div>
          <p className='text-sm font-medium'>{t('settings.model_config.ot3_account_tools')}</p>
          <p className='text-muted-foreground text-xs'>
            {t('settings.model_config.ot3_account_tools_desc')}
          </p>
        </div>
        <div className='flex flex-wrap gap-2'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('health')}
            disabled={Boolean(busy) || !settings.ot.server}
          >
            {busy === 'health' ? <Loader2 className='animate-spin' /> : <Activity />}
            {t('settings.model_config.health_check')}
          </Button>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('key')}
            disabled={Boolean(busy) || !settings.ot.server || !settings.ot.api_key}
          >
            {busy === 'key' ? <Loader2 className='animate-spin' /> : <KeyRound />}
            {t('settings.model_config.query_key')}
          </Button>
          <Button
            size='sm'
            variant='outline'
            onClick={() => void run('models')}
            disabled={Boolean(busy) || !settings.ot.server || !settings.ot.api_key}
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

      <div className='grid grid-cols-2 gap-3'>
        <div>
          <p className='mb-1 text-xs font-medium'>{t('settings.model_config.ot3_model_4p')}</p>
          <Select
            value={settings.ot.model_4p || '__default'}
            onValueChange={(value) => updateModel('4p', value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__default'>{t('settings.model_config.server_default')}</SelectItem>
              {fourPlayerModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.id}
                  {model.desc ? ` · ${model.desc}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <p className='mb-1 text-xs font-medium'>{t('settings.model_config.ot3_model_3p')}</p>
          <Select
            value={settings.ot.model_3p || '__default'}
            onValueChange={(value) => updateModel('3p', value)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__default'>{t('settings.model_config.server_default')}</SelectItem>
              {threePlayerModels.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.id}
                  {model.desc ? ` · ${model.desc}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

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
