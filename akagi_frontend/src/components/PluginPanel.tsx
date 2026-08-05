import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  PlugZap,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CapsuleSwitch } from '@/components/ui/capsule-switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { fetchPluginsApi, setPluginEnabledApi } from '@/lib/plugins-api';
import type { PluginInfo } from '@/types';

interface PluginPanelProps {
  open: boolean;
  onClose: () => void;
}

const statusIcons = {
  active: CheckCircle2,
  disabled: PlugZap,
  waiting_for_mitm: AlertTriangle,
  error: ShieldAlert,
} as const;

export default function PluginPanel({ open, onClose }: PluginPanelProps) {
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updatingId, setUpdatingId] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPlugins(await fetchPluginsApi());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const togglePlugin = async (plugin: PluginInfo, enabled: boolean) => {
    setUpdatingId(plugin.id);
    try {
      const result = await setPluginEnabledApi(plugin.id, enabled);
      if (result.data) {
        setPlugins((current) =>
          current.map((item) => (item.id === plugin.id ? result.data! : item)),
        );
      }
      if (result.proxyError) {
        toast.warning(result.proxyError);
      } else {
        if (result.proxyChanged) {
          const status = await window.electron.reconcileMihomo(true);
          if (status.error) toast.warning(status.error);
          if (status.gameWindowClosed) toast.info(t('plugins.game_closed_for_proxy'));
        }
        toast.success(t(enabled ? 'plugins.enabled_success' : 'plugins.disabled_success'));
      }
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : String(reason));
      await refresh();
    } finally {
      setUpdatingId('');
    }
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className='flex max-h-[85vh] flex-col gap-0 p-0 sm:max-w-3xl'>
        <DialogHeader className='border-border border-b p-6 pb-4'>
          <DialogTitle className='flex items-center gap-2'>
            <PlugZap className='h-5 w-5 text-violet-500' />
            {t('plugins.title')}
          </DialogTitle>
          <DialogDescription>{t('plugins.description')}</DialogDescription>
        </DialogHeader>

        <div className='flex-1 space-y-4 overflow-y-auto p-6'>
          {loading && plugins.length === 0 && (
            <div className='text-muted-foreground flex items-center justify-center gap-2 py-12 text-sm'>
              <Loader2 className='h-4 w-4 animate-spin' />
              {t('plugins.loading')}
            </div>
          )}

          {error && (
            <Alert variant='error'>
              <ShieldAlert className='h-4 w-4' />
              <AlertDescription className='flex items-center justify-between gap-4'>
                <span>{error}</span>
                <Button variant='outline' size='sm' onClick={() => void refresh()}>
                  <RefreshCw className='mr-2 h-4 w-4' />
                  {t('plugins.retry')}
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {plugins.map((plugin) => {
            const StatusIcon = statusIcons[plugin.runtime_status];
            const updating = updatingId === plugin.id;
            return (
              <section
                key={plugin.id}
                className='border-border bg-card/70 space-y-4 rounded-xl border p-5 shadow-sm'
              >
                <div className='flex items-start justify-between gap-6'>
                  <div className='min-w-0 space-y-1'>
                    <div className='flex flex-wrap items-center gap-2'>
                      <h3 className='font-semibold'>{plugin.name}</h3>
                      <Badge variant='secondary'>{plugin.version}</Badge>
                      <Badge
                        variant={plugin.runtime_status === 'error' ? 'destructive' : 'outline'}
                      >
                        <StatusIcon className='mr-1 h-3 w-3' />
                        {t(`plugins.status.${plugin.runtime_status}`)}
                      </Badge>
                    </div>
                    <p className='text-muted-foreground text-sm'>{plugin.description}</p>
                    <p className='text-muted-foreground text-xs'>
                      {t('plugins.author')}: {plugin.author}
                    </p>
                  </div>
                  <CapsuleSwitch
                    checked={plugin.enabled}
                    disabled={updating}
                    onCheckedChange={(enabled) => void togglePlugin(plugin, enabled)}
                    labelOn={
                      updating ? <Loader2 className='h-4 w-4 animate-spin' /> : t('common.enabled')
                    }
                    labelOff={t('common.disabled')}
                    className='min-w-36 shrink-0'
                  />
                </div>

                <div className='flex flex-wrap gap-2'>
                  {plugin.capabilities.map((capability) => (
                    <Badge key={capability} variant='outline' className='font-mono text-[11px]'>
                      {capability}
                    </Badge>
                  ))}
                </div>

                {plugin.error && (
                  <Alert variant='error'>
                    <ShieldAlert className='h-4 w-4' />
                    <AlertDescription>{plugin.error}</AlertDescription>
                  </Alert>
                )}

                {plugin.risk_notice && (
                  <Alert variant='warning'>
                    <AlertTriangle className='h-4 w-4' />
                    <AlertDescription>{plugin.risk_notice}</AlertDescription>
                  </Alert>
                )}

                <div className='flex items-center justify-between gap-4'>
                  <p className='text-muted-foreground text-xs'>
                    {plugin.requires_mitm
                      ? t('plugins.mitm_required')
                      : t('plugins.no_mitm_required')}
                  </p>
                  <Button
                    variant='ghost'
                    size='sm'
                    onClick={() => void window.electron.openExternal(plugin.homepage)}
                  >
                    <ExternalLink className='mr-2 h-4 w-4' />
                    {t('plugins.homepage')}
                  </Button>
                </div>
              </section>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}
