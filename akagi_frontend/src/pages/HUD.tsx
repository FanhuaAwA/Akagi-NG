import { MousePointer2, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import StreamPlayer from '@/components/StreamPlayer';
import { HudControlButton } from '@/components/ui/hud-control-button';
import { ModelStatusIndicator } from '@/components/ui/model-status-indicator';
import { cn } from '@/lib/utils';

interface HudClickThroughStatus {
  available: boolean;
  enabled: boolean;
}

export default function Hud() {
  const { t } = useTranslation();
  const [clickThrough, setClickThrough] = useState<HudClickThroughStatus>({
    available: false,
    enabled: false,
  });

  useEffect(() => {
    window.electron.invoke<HudClickThroughStatus>('hud-click-through-status').then(setClickThrough);
    const unsubscribe = window.electron.on(
      'hud-click-through-changed',
      (status: HudClickThroughStatus) => setClickThrough(status),
    );
    return () => {
      unsubscribe?.();
      void window.electron.invoke('hud-set-controls-interactive', false);
    };
  }, []);

  const setControlsInteractive = (interactive: boolean) => {
    void window.electron.invoke('hud-set-controls-interactive', interactive);
  };

  return (
    <div className='draggable relative h-screen w-full overflow-hidden'>
      <StreamPlayer className='h-full w-full' />

      {/* Model Status Indicator */}
      <ModelStatusIndicator className='top-3 left-3' />

      {/* This control island stays interactive while the rest of the HUD passes clicks through. */}
      <div
        className='no-drag z-hud absolute top-1 right-1 flex gap-1 rounded-full p-1'
        onPointerEnter={() => setControlsInteractive(true)}
        onPointerLeave={() => setControlsInteractive(false)}
      >
        {clickThrough.available && (
          <HudControlButton
            className={cn(clickThrough.enabled && 'bg-emerald-500/40 opacity-100')}
            title={
              clickThrough.enabled
                ? t('app.hud_click_through_disable')
                : t('app.hud_click_through_enable')
            }
            aria-label={
              clickThrough.enabled
                ? t('app.hud_click_through_disable')
                : t('app.hud_click_through_enable')
            }
            aria-pressed={clickThrough.enabled}
            onClick={async () => {
              const status = await window.electron.invoke<HudClickThroughStatus>(
                'hud-set-click-through',
                !clickThrough.enabled,
              );
              setClickThrough(status);
            }}
          >
            <MousePointer2 className='h-4 w-4' />
          </HudControlButton>
        )}
        <HudControlButton
          title={t('common.close')}
          aria-label={t('common.close')}
          onClick={() => window.electron.invoke('toggle-hud', false)}
        >
          <X className='h-4 w-4' />
        </HudControlButton>
      </div>
    </div>
  );
}
