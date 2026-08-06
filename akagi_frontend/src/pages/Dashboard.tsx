import { CircleAlert, LoaderCircle } from 'lucide-react';
import { use, useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { ToastContainer } from 'react-toastify';

import AutoplayPanel from '@/components/AutoplayPanel';
import { Footer } from '@/components/layout/Footer';
import { Header } from '@/components/layout/Header';
import LogPanel from '@/components/LogPanel';
import PluginPanel from '@/components/PluginPanel';
import SettingsPanel from '@/components/SettingsPanel';
import StreamPlayer from '@/components/StreamPlayer';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { TOAST_DURATION_DEFAULT } from '@/config/constants';
import { MITM_REQUIRED_PLATFORMS } from '@/config/platforms';
import { GameContext } from '@/contexts/GameContext';
import { fetchSettingsApi, useSettings } from '@/hooks/useSettings';
import { useTheme } from '@/hooks/useTheme';
import { notify } from '@/lib/notify';
import { fetchPluginsApi } from '@/lib/plugins-api';
import { cn } from '@/lib/utils';
interface DashboardProps {
  backendState: { status: 'starting' | 'ready'; error: null } | { status: 'error'; error: string };
  isSplashActive?: boolean;
}

function Dashboard({ backendState, isSplashActive = false }: DashboardProps) {
  const { t, i18n } = useTranslation();
  const { theme } = useTheme();

  const context = use(GameContext);
  if (!context) throw new Error('GameContext not found');

  const { updateSetting, settings } = useSettings();
  const { setIsHudActive, isHudActive } = context;

  const handleLocaleChange = useCallback(
    async (newLocale: string) => {
      updateSetting(['locale'], newLocale);
    },
    [updateSetting],
  );

  const backendReady = backendState.status === 'ready';
  const isLaunchDisabled =
    !backendReady ||
    (MITM_REQUIRED_PLATFORMS.includes(settings.platform) && !settings.mitm.enabled);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [autoplayOpen, setAutoplayOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [showShutdownConfirm, setShowShutdownConfirm] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);

  const [resourceStatus, setResourceStatus] = useState<{
    lib: boolean;
    models: boolean;
  } | null>(null);

  // 1. 订阅系统主题
  const isSystemDark = useSyncExternalStore(
    (callback) => {
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      media.addEventListener('change', callback);
      return () => media.removeEventListener('change', callback);
    },
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
    () => false,
  );

  // 2. 业务副作用：资源检查与 HUD 监听
  useEffect(() => {
    window.electron.checkResourceStatus().then((status) => {
      setResourceStatus(status);
    });

    const unsubHud = window.electron.onHudVisibilityChanged((visible: boolean) => {
      setIsHudActive(visible);
    });

    const unsubQuit = window.electron.onRequestAppQuit(() => {
      setShowShutdownConfirm(true);
    });

    return () => {
      if (unsubHud) unsubHud();
      if (unsubQuit) unsubQuit();
    };
  }, [setIsHudActive, t]);

  // 3. 资源状态通知
  useEffect(() => {
    if (!resourceStatus) return;

    const { lib, models } = resourceStatus;
    const { ot } = settings;

    if (!lib) {
      notify.error(t('status_messages.lib_missing'), { toastId: 'lib_missing', autoClose: false });
    }
    if (!models && !ot.online) {
      notify.warn(t('status_messages.models_missing'), {
        toastId: 'models_missing',
        autoClose: false,
      });
    }
  }, [resourceStatus, t, settings]);

  const handleLaunchGame = useCallback(async () => {
    setIsLaunching(true);
    try {
      const [currentSettings, plugins] = await Promise.all([
        fetchSettingsApi().catch(() => settings),
        fetchPluginsApi().catch(() => []),
      ]);
      const pluginNeedsMitm = plugins.some((plugin) => plugin.enabled && plugin.requires_mitm);
      await window.electron.startGame({
        url: currentSettings.game_url,
        useMitm: currentSettings.mitm.enabled || pluginNeedsMitm,
        platform: currentSettings.platform,
      });
    } catch (e) {
      console.error('Failed to start game window:', e);
      notify.error(t('app.launch_error'));
    } finally {
      setIsLaunching(false);
    }
  }, [settings, t]);

  const handleShutdownClick = useCallback(() => {
    window.electron.closeDashboard();
  }, []);

  const performShutdown = useCallback(async () => {
    try {
      await window.electron.requestShutdown();
    } catch (e) {
      console.error('Failed to shutdown:', e);
      notify.error(`${t('common.error')}: ${(e as Error).message}`);
    }
  }, [t]);

  const handleOpenSettings = useCallback(() => setSettingsOpen(true), []);
  const handleCloseSettings = useCallback(() => setSettingsOpen(false), []);
  const handleToggleHud = useCallback(
    (show: boolean) => {
      window.electron.toggleHud(show);
      setIsHudActive(show);
    },
    [setIsHudActive],
  );

  return (
    <>
      <div
        className={cn(
          'flex h-full flex-col transition duration-300',
          isSplashActive ? 'pointer-events-none opacity-0 blur-xl' : 'blur-0 opacity-100',
        )}
      >
        <Header
          isLaunching={isLaunching}
          isLaunchDisabled={isLaunchDisabled}
          onLaunch={handleLaunchGame}
          onOpenSettings={handleOpenSettings}
          onOpenAutoplay={() => setAutoplayOpen(true)}
          onOpenLogs={() => setLogsOpen(true)}
          onOpenPlugins={() => setPluginsOpen(true)}
          locale={i18n.language}
          onLocaleChange={handleLocaleChange}
          onShutdown={handleShutdownClick}
          onToggleHud={handleToggleHud}
          isHudActive={isHudActive}
          controlsDisabled={!backendReady}
        />
        <main className='relative flex w-full grow overflow-hidden px-6 py-4'>
          <StreamPlayer className='h-full w-full' />
          {!backendReady && (
            <div
              className='bg-background/90 absolute right-10 bottom-8 left-10 flex items-center gap-3 rounded-xl border px-4 py-3 shadow-lg backdrop-blur-sm'
              role='status'
              aria-live='polite'
            >
              {backendState.status === 'error' ? (
                <CircleAlert className='text-destructive h-5 w-5 shrink-0' />
              ) : (
                <LoaderCircle className='h-5 w-5 shrink-0 animate-spin text-violet-500' />
              )}
              <div className='min-w-0'>
                <p className='text-sm font-medium'>
                  {t(
                    backendState.status === 'error'
                      ? 'app.backend_start_failed'
                      : 'app.backend_starting',
                  )}
                </p>
                <p className='text-muted-foreground truncate text-xs'>
                  {backendState.status === 'error'
                    ? backendState.error
                    : t('app.backend_starting_desc')}
                </p>
              </div>
            </div>
          )}
        </main>

        <Footer />
      </div>

      <SettingsPanel open={settingsOpen} onClose={handleCloseSettings} />
      <AutoplayPanel open={autoplayOpen} onClose={() => setAutoplayOpen(false)} />
      <LogPanel open={logsOpen} onClose={() => setLogsOpen(false)} />
      <PluginPanel open={pluginsOpen} onClose={() => setPluginsOpen(false)} />

      <AlertDialog open={showShutdownConfirm} onOpenChange={setShowShutdownConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('app.shutdown_confirm_title')}</AlertDialogTitle>
            <AlertDialogDescription>{t('app.shutdown_confirm_desc')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant='destructive'
              onClick={() => {
                performShutdown();
                setShowShutdownConfirm(false);
              }}
            >
              {t('common.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ToastContainer
        autoClose={TOAST_DURATION_DEFAULT}
        closeOnClick
        position='top-right'
        theme={theme === 'system' ? (isSystemDark ? 'dark' : 'light') : theme}
      />
    </>
  );
}

export default Dashboard;
