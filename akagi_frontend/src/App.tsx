import 'react-toastify/dist/ReactToastify.css';

import { lazy, Suspense, use, useEffect, useMemo, useState } from 'react';
import { HashRouter, Route, Routes } from 'react-router';

import { ConnectionProvider } from '@/components/ConnectionProvider';
import { ExitOverlay } from '@/components/ExitOverlay';
import { GameProvider } from '@/components/GameProvider';
import { LaunchScreen } from '@/components/LaunchScreen';
import { SettingsProvider } from '@/components/SettingsProvider';
import { StartupErrorBoundary } from '@/components/StartupErrorBoundary';
import { ThemeProvider } from '@/components/ThemeProvider';
import { APP_SPLASH_EXIT_MS } from '@/config/constants';
import { fetchSettingsApi } from '@/hooks/useSettings';
import { setBaseUrl } from '@/lib/api-client';
import { cn } from '@/lib/utils';

const Dashboard = lazy(() => import('@/pages/Dashboard'));
const Hud = lazy(() => import('@/pages/HUD'));

/**
 * 带有中止信号的等待工具函数
 */
const wait = (ms: number, signal?: AbortSignal) =>
  new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener('abort', () => clearTimeout(timer), { once: true });
    }
  });

/**
 * 应用程序初始化逻辑
 */
const loadStartupConfig = async () => {
  if (!window.electron) {
    throw new Error('Akagi-NG requires Electron environment to boot.');
  }
  const { host, port, settings } = await window.electron.getStartupConfig();
  const apiBase = `http://${host}:${port}`;
  setBaseUrl(apiBase);
  return { host, port, settings, apiBase };
};

// 预加载应用数据
const startupConfigPromise = loadStartupConfig();
const backendSettingsPromise = startupConfigPromise.then(async () => {
  // Full packaged-resource hashing and Python imports continue after the first
  // frame. They still gate backend execution; they no longer gate the dashboard.
  const { host, port } = await window.electron.waitForBackend(60_000);
  setBaseUrl(`http://${host}:${port}`);
  return await fetchSettingsApi();
});
void backendSettingsPromise.catch(() => {});

function AppInner() {
  const data = use(startupConfigPromise);
  const isHud = useMemo(() => window.location.hash === '#/hud', []);
  const [isExiting, setIsExiting] = useState(false);
  const [backendState, setBackendState] = useState<
    { status: 'starting' | 'ready'; error: null } | { status: 'error'; error: string }
  >({ status: 'starting', error: null });

  const [splashStage, setSplashStage] = useState<'splash' | 'exiting' | 'ready'>(
    isHud ? 'ready' : 'splash',
  );

  useEffect(() => {
    document.documentElement.classList.toggle('is-hud', isHud);
    return () => {
      document.documentElement.classList.remove('is-hud');
    };
  }, [isHud]);

  useEffect(() => {
    if (isHud) return;

    const ac = new AbortController();
    (async () => {
      try {
        setSplashStage('exiting');
        await wait(APP_SPLASH_EXIT_MS, ac.signal);
        setSplashStage('ready');
      } catch {
        /* Aborted */
      }
    })();

    return () => ac.abort();
  }, [isHud]);

  useEffect(() => {
    if (!window.electron) return;
    const unsubExit = window.electron.onExitAnimationStart(() => setIsExiting(true));
    return () => unsubExit();
  }, []);

  useEffect(() => {
    let active = true;
    void backendSettingsPromise.then(
      () => {
        if (active) setBackendState({ status: 'ready', error: null });
      },
      (error: unknown) => {
        if (!active) return;
        setBackendState({
          status: 'error',
          error: error instanceof Error ? error.message : String(error),
        });
      },
    );
    return () => {
      active = false;
    };
  }, []);

  return (
    <ConnectionProvider host={data.host} port={data.port} apiBase={data.apiBase}>
      <SettingsProvider
        initialSettings={data.settings}
        resolvedSettingsPromise={backendSettingsPromise}
      >
        <GameProvider backendReady={backendState.status === 'ready'}>
          <HashRouter>
            <Routes>
              <Route
                path='/'
                element={
                  <Dashboard
                    backendState={backendState}
                    isSplashActive={!isHud && splashStage === 'splash'}
                  />
                }
              />
              <Route path='/hud' element={<Hud />} />
            </Routes>
          </HashRouter>
        </GameProvider>
      </SettingsProvider>

      {!isHud && splashStage !== 'ready' && (
        <LaunchScreen
          isStatic={true}
          className={cn(
            splashStage === 'exiting' &&
              'animate-out fade-out-0 zoom-out-95 fill-mode-forwards duration-300',
          )}
        />
      )}

      {isExiting && <ExitOverlay />}
    </ConnectionProvider>
  );
}

export default function App() {
  const isHud = window.location.hash === '#/hud';

  return (
    <ThemeProvider>
      <StartupErrorBoundary>
        <Suspense fallback={isHud ? <div className='h-full' /> : <LaunchScreen />}>
          <AppInner />
        </Suspense>
      </StartupErrorBoundary>
    </ThemeProvider>
  );
}
