import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  FolderUp,
  Loader2,
  PlugZap,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
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
import {
  fetchPluginCatalogApi,
  fetchPluginsApi,
  importLocalPluginApi,
  installRemotePluginApi,
  setPluginEnabledApi,
} from '@/lib/plugins-api';
import type { PluginCatalogEntry, PluginInfo } from '@/types';

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
  const [catalog, setCatalog] = useState<PluginCatalogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [updatingId, setUpdatingId] = useState('');
  const [installingId, setInstallingId] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    setCatalogError('');
    const [installedResult, catalogResult] = await Promise.allSettled([
      fetchPluginsApi(),
      fetchPluginCatalogApi(),
    ]);
    if (installedResult.status === 'fulfilled') setPlugins(installedResult.value);
    else
      setError(
        installedResult.reason instanceof Error
          ? installedResult.reason.message
          : String(installedResult.reason),
      );
    if (catalogResult.status === 'fulfilled') setCatalog(catalogResult.value);
    else
      setCatalogError(
        catalogResult.reason instanceof Error
          ? catalogResult.reason.message
          : String(catalogResult.reason),
      );
    setLoading(false);
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

  const installRemote = async (entry: PluginCatalogEntry) => {
    setInstallingId(entry.id);
    try {
      await installRemotePluginApi(entry.id, entry.update_available);
      toast.success(
        t(entry.update_available ? 'plugins.updated_success' : 'plugins.installed_success'),
      );
      await refresh();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setInstallingId('');
    }
  };

  const importLocal = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const archive = event.target.files?.[0];
    event.target.value = '';
    if (!archive) return;
    setInstallingId('local');
    try {
      await importLocalPluginApi(archive);
      toast.success(t('plugins.imported_success'));
      await refresh();
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setInstallingId('');
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
          <div className='flex flex-wrap items-center justify-between gap-3'>
            <div>
              <h3 className='font-semibold'>{t('plugins.repository_title')}</h3>
              <p className='text-muted-foreground text-xs'>{t('plugins.repository_desc')}</p>
            </div>
            <div className='flex gap-2'>
              <input
                ref={fileInputRef}
                type='file'
                accept='.zip,.akagi-plugin'
                className='hidden'
                onChange={(event) => void importLocal(event)}
              />
              <Button
                variant='outline'
                size='sm'
                disabled={installingId === 'local'}
                onClick={() => fileInputRef.current?.click()}
              >
                {installingId === 'local' ? (
                  <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                ) : (
                  <FolderUp className='mr-2 h-4 w-4' />
                )}
                {t('plugins.import_local')}
              </Button>
              <Button variant='outline' size='sm' onClick={() => void refresh()}>
                <RefreshCw className='mr-2 h-4 w-4' />
                {t('plugins.refresh_repository')}
              </Button>
            </div>
          </div>

          {catalogError && (
            <Alert variant='warning'>
              <AlertTriangle className='h-4 w-4' />
              <AlertDescription>{catalogError}</AlertDescription>
            </Alert>
          )}

          {catalog.map((entry) => {
            const installing = installingId === entry.id;
            return (
              <section
                key={`catalog-${entry.id}`}
                className='border-border bg-muted/30 rounded-xl border p-4'
              >
                <div className='flex items-start justify-between gap-4'>
                  <div className='min-w-0 space-y-1'>
                    <div className='flex flex-wrap items-center gap-2'>
                      <span className='font-medium'>{entry.name}</span>
                      <Badge variant='secondary'>{entry.version}</Badge>
                      {entry.installed && <Badge variant='outline'>{t('plugins.installed')}</Badge>}
                    </div>
                    <p className='text-muted-foreground text-sm'>{entry.description}</p>
                    {entry.conflicts.length > 0 && (
                      <p className='text-xs text-amber-600'>
                        {t('plugins.conflicts')}: {entry.conflicts.join(', ')}
                      </p>
                    )}
                  </div>
                  <Button
                    size='sm'
                    disabled={installing || (entry.installed && !entry.update_available)}
                    onClick={() => void installRemote(entry)}
                  >
                    {installing ? (
                      <Loader2 className='mr-2 h-4 w-4 animate-spin' />
                    ) : (
                      <Download className='mr-2 h-4 w-4' />
                    )}
                    {entry.update_available
                      ? t('plugins.update')
                      : entry.installed
                        ? t('plugins.installed')
                        : t('plugins.install')}
                  </Button>
                </div>
              </section>
            );
          })}

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
