import {
  Bot,
  Copy,
  ExternalLink,
  Globe,
  Minus,
  PictureInPicture,
  Puzzle,
  RefreshCw,
  ScrollText,
  SettingsIcon,
  Square,
  X,
} from 'lucide-react';
import type { ComponentProps } from 'react';
import { use, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { InferenceStatusIndicator } from '@/components/ui/inference-status-indicator';
import { ModelStatusIndicator } from '@/components/ui/model-status-indicator';
import { NavbarActionButton as HeaderIconButton } from '@/components/ui/navbar-action-button';
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select';
import { ThemeToggle } from '@/components/ui/theme-toggle';
import { SUPPORTED_LOCALES } from '@/config/locales';
import { GameContext } from '@/contexts/GameContext';
import { useTheme } from '@/hooks/useTheme';
import { cn } from '@/lib/utils';

type HeaderFeatureButtonProps = Omit<ComponentProps<typeof HeaderIconButton>, 'title'> & {
  tooltip: string;
};

function HeaderFeatureButton({ tooltip, ...props }: HeaderFeatureButtonProps) {
  return (
    <div className='group relative flex h-full items-center'>
      <HeaderIconButton {...props} />
      <span
        role='tooltip'
        className='pointer-events-none invisible absolute top-[calc(100%+0.4rem)] left-1/2 z-50 -translate-x-1/2 -translate-y-1 rounded-md bg-zinc-900/90 px-2 py-1 text-xs font-medium whitespace-nowrap text-white opacity-0 shadow-lg backdrop-blur-sm transition-[opacity,transform,visibility] delay-0 duration-150 group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100 group-hover:delay-500 dark:bg-zinc-100/90 dark:text-zinc-900'
      >
        {tooltip}
      </span>
    </div>
  );
}

interface HeaderProps {
  isLaunching: boolean;
  isLaunchDisabled?: boolean;
  onLaunch: () => void;
  onOpenSettings: () => void;
  onOpenAutoplay: () => void;
  onOpenLogs: () => void;
  onOpenPlugins: () => void;
  locale?: string;
  onLocaleChange?: (locale: string) => void;
  onShutdown?: () => void;
  onToggleHud?: (show: boolean) => void;
  isHudActive?: boolean;
  isConnected: boolean;
  controlsDisabled?: boolean;
}

function HeaderContent({
  isLaunching,
  isLaunchDisabled = false,
  onLaunch,
  onOpenSettings,
  onOpenAutoplay,
  onOpenLogs,
  onOpenPlugins,
  locale,
  onLocaleChange,
  onShutdown,
  onToggleHud,
  isHudActive = false,
  isConnected,
  controlsDisabled = false,
}: HeaderProps) {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const [isMaximized, setIsMaximized] = useState(false);

  useEffect(() => {
    const unsub = window.electron.onDashboardMaximizedChanged((maximized: boolean) => {
      setIsMaximized(maximized);
    });
    window.electron.isDashboardMaximized().then((maximized) => {
      setIsMaximized(maximized);
    });
    return unsub;
  }, []);

  return (
    <header className='draggable z-header bg-linear-to-b from-white/50 to-transparent dark:from-black/50 dark:to-transparent'>
      <div className='flex h-16 items-center justify-between px-6'>
        {/* Logo & Title */}
        <div className='flex items-center gap-3'>
          {/* Status Indicator */}
          <div className='relative flex h-2.5 w-2.5 items-center justify-center'>
            <ModelStatusIndicator isConnected={isConnected} className='static' />
          </div>
          <h1 className='bg-linear-to-r from-pink-600 to-violet-600 bg-clip-text text-xl font-bold text-transparent dark:from-pink-400 dark:to-violet-400'>
            {t('app.title')}
          </h1>
          <InferenceStatusIndicator />
        </div>

        {/* Actions - Control Group */}
        <div className='flex h-9 items-center gap-1'>
          {/* Launch Button */}
          <Button
            variant='ghost'
            size='sm'
            className='no-drag text-muted-foreground hover:bg-accent hover:text-foreground flex h-full rounded-md px-3 transition-colors'
            onClick={onLaunch}
            disabled={controlsDisabled || isLaunching || isLaunchDisabled}
          >
            {isLaunching ? (
              <RefreshCw className='mr-2 h-4 w-4 animate-spin' />
            ) : (
              <ExternalLink className='mr-2 h-4 w-4' />
            )}
            {t('app.launch_game')}
          </Button>

          {/* Language Switcher */}
          {locale && onLocaleChange && (
            <Select value={locale} onValueChange={onLocaleChange} disabled={controlsDisabled}>
              <SelectTrigger className='no-drag text-muted-foreground hover:bg-accent hover:text-foreground aspect-square h-full justify-center rounded-md border-none bg-transparent p-0 shadow-none transition-colors focus:ring-0 focus:ring-offset-0 [&>svg:last-child]:hidden'>
                <Globe className='h-4 w-4' />
              </SelectTrigger>
              <SelectContent align='end'>
                {SUPPORTED_LOCALES.map((loc) => (
                  <SelectItem key={loc.value} value={loc.value}>
                    {loc.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {/* Theme Toggle */}
          <ThemeToggle theme={theme} setTheme={setTheme} />

          {/* Settings Button */}
          <HeaderFeatureButton
            icon={Puzzle}
            onClick={onOpenPlugins}
            aria-label={t('plugins.open')}
            tooltip={t('plugins.title')}
            disabled={controlsDisabled}
          />

          <HeaderFeatureButton
            icon={Bot}
            onClick={onOpenAutoplay}
            aria-label={t('autoplay_page.open')}
            tooltip={t('autoplay_page.title')}
            disabled={controlsDisabled}
          />

          <HeaderFeatureButton
            icon={ScrollText}
            onClick={onOpenLogs}
            aria-label={t('logs_page.open')}
            tooltip={t('logs_page.title')}
            disabled={controlsDisabled}
          />

          <HeaderFeatureButton
            icon={SettingsIcon}
            onClick={onOpenSettings}
            aria-label='Open settings'
            tooltip={t('app.settings_title')}
            disabled={controlsDisabled}
          />

          {/* HUD Toggle Button */}
          {onToggleHud && (
            <HeaderIconButton
              icon={PictureInPicture}
              onClick={() => onToggleHud(!isHudActive)}
              className={cn(
                isHudActive &&
                  'bg-violet-100 text-violet-600 hover:bg-violet-200 dark:bg-violet-900/30 dark:text-violet-400 dark:hover:bg-violet-900/50',
              )}
              aria-label='Toggle HUD'
              disabled={controlsDisabled}
            />
          )}

          {/* Window Controls */}
          <HeaderIconButton
            icon={Minus}
            onClick={() => window.electron.minimizeDashboard()}
            aria-label='Minimize'
          />

          <HeaderIconButton
            icon={isMaximized ? Copy : Square}
            iconClassName={isMaximized ? '-scale-x-100 -rotate-90' : ''}
            onClick={() => window.electron.toggleDashboardMaximized()}
            aria-label={isMaximized ? 'Restore' : 'Maximize'}
          />

          {/* Shutdown Button */}
          {onShutdown && (
            <HeaderIconButton
              icon={X}
              onClick={onShutdown}
              className='text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:text-rose-400 dark:hover:bg-rose-950/40 dark:hover:text-rose-300'
              aria-label='Shutdown'
            />
          )}
        </div>
      </div>
    </header>
  );
}

export function Header(props: Omit<HeaderProps, 'isConnected'>) {
  const gameContext = use(GameContext);
  if (!gameContext) throw new Error('GameContext not found');
  const { isConnected } = gameContext;

  return <HeaderContent {...props} isConnected={isConnected} />;
}
