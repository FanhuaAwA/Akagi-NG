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
import { APP_SPLASH_EXIT_MS, APP_SPLASH_SHOW_MS } from '@/config/constants';
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
const initApp = async () => {
  if (!window.electron) {
    throw new Error('Akagi-NG requires Electron environment to boot.');
  }
  const { host, port } = await window.electron.invoke<{ host: string; port: number }>(
    'wait-for-backend',
  );
  const apiBase = `http://${host}:${port}`;
  setBaseUrl(apiBase);
  const settings = await fetchSettingsApi();
  return { host, port, settings, apiBase };
};

// 预加载应用数据
const appDataPromise = initApp();
const settingsResolvedPromise = appDataPromise.then((d) => d.settings);

function AppInner() {
  const data = use(appDataPromise);
  const isHud = useMemo(() => window.location.hash === '#/hud', []);
  const [isExiting, setIsExiting] = useState(false);

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
        await wait(APP_SPLASH_SHOW_MS, ac.signal);
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
    const unsubExit = window.electron.on('exit-animation-start', () => setIsExiting(true));
    return () => unsubExit();
  }, []);

  return (
    <ConnectionProvider host={data.host} port={data.port} apiBase={data.apiBase}>
      <SettingsProvider initialSettings={data.settings}>
        <GameProvider>
          <HashRouter>
            <Routes>
              <Route
                path='/'
                element={
                  <Dashboard
                    settingsPromise={settingsResolvedPromise}
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
              'animate-out fade-out-0 zoom-out-95 fill-mode-forwards duration-1000',
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
