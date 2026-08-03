import { use, useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { useTranslation } from 'react-i18next';
import { ToastContainer } from 'react-toastify';

import { Footer } from '@/components/layout/Footer';
import { Header } from '@/components/layout/Header';
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
import { cn } from '@/lib/utils';
import type { Settings } from '@/types';

interface DashboardProps {
  settingsPromise: Promise<Settings>;
  isSplashActive?: boolean;
}

function Dashboard({ settingsPromise, isSplashActive = false }: DashboardProps) {
  const { t, i18n } = useTranslation();
  const { theme } = useTheme();
  const initialSettings = use(settingsPromise);

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

  const isLaunchDisabled =
    MITM_REQUIRED_PLATFORMS.includes(settings.platform) && !settings.mitm.enabled;

  const [settingsOpen, setSettingsOpen] = useState(false);
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
    const { ot } = initialSettings;

    if (!lib) {
      notify.error(t('status_messages.lib_missing'), { toastId: 'lib_missing', autoClose: false });
    }
    if (!models && !ot.online) {
      notify.warn(t('status_messages.models_missing'), {
        toastId: 'models_missing',
        autoClose: false,
      });
    }
  }, [resourceStatus, t, initialSettings]);

  const handleLaunchGame = useCallback(async () => {
    setIsLaunching(true);
    try {
      const currentSettings = await fetchSettingsApi().catch(() => initialSettings);
      await window.electron.startGame({
        url: currentSettings.game_url,
        useMitm: currentSettings.mitm.enabled,
        platform: currentSettings.platform,
      });
    } catch (e) {
      console.error('Failed to start game window:', e);
      notify.error(t('app.launch_error'));
    } finally {
      setIsLaunching(false);
    }
  }, [initialSettings, t]);

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
          'flex h-full flex-col transition duration-1000',
          isSplashActive ? 'pointer-events-none opacity-0 blur-xl' : 'blur-0 opacity-100',
        )}
      >
        <Header
          isLaunching={isLaunching}
          isLaunchDisabled={isLaunchDisabled}
          onLaunch={handleLaunchGame}
          onOpenSettings={handleOpenSettings}
          locale={i18n.language}
          onLocaleChange={handleLocaleChange}
          onShutdown={handleShutdownClick}
          onToggleHud={handleToggleHud}
          isHudActive={isHudActive}
        />
        <main className='flex w-full grow overflow-hidden px-6 py-4'>
          <StreamPlayer className='h-full w-full' />
        </main>

        <Footer />
      </div>

      <SettingsPanel open={settingsOpen} onClose={handleCloseSettings} />

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
        position='top-right'
        theme={theme === 'system' ? (isSystemDark ? 'dark' : 'light') : theme}
      />
    </>
  );
}

export default Dashboard;
