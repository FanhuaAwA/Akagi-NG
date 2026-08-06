import { Bot, LogIn } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AutoJoinSection } from '@/components/settings/AutoJoinSection';
import { AutoplaySection } from '@/components/settings/AutoplaySection';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useSettings } from '@/hooks/useSettings';

interface AutoplayPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function AutoplayPanel({ open, onClose }: AutoplayPanelProps) {
  const { t } = useTranslation();
  const { settings, updateSetting, refreshSettings } = useSettings();
  const [activePage, setActivePage] = useState<'play' | 'join'>('play');

  useEffect(() => {
    if (!open) return;
    void refreshSettings();
  }, [open, refreshSettings]);

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className='flex max-h-[92vh] flex-col gap-0 p-0 sm:max-w-5xl'>
        <DialogHeader className='border-border border-b p-6 pb-4'>
          <DialogTitle>{t('autoplay_page.title')}</DialogTitle>
          <DialogDescription>{t('autoplay_page.description')}</DialogDescription>
        </DialogHeader>
        <div className='border-border bg-muted/20 grid grid-cols-2 gap-2 border-b p-3'>
          <Button
            variant={activePage === 'play' ? 'default' : 'ghost'}
            className='h-auto justify-start gap-3 px-4 py-3'
            onClick={() => setActivePage('play')}
          >
            <Bot className='h-4 w-4' />
            <span className='text-left'>
              <span className='block'>{t('autoplay_page.play_tab')}</span>
              <span className='block text-xs font-normal opacity-70'>
                {settings.autoplay.enabled ? t('common.enabled') : t('common.disabled')}
              </span>
            </span>
          </Button>
          <Button
            variant={activePage === 'join' ? 'default' : 'ghost'}
            className='h-auto justify-start gap-3 px-4 py-3'
            onClick={() => setActivePage('join')}
          >
            <LogIn className='h-4 w-4' />
            <span className='text-left'>
              <span className='block'>{t('autoplay_page.join_tab')}</span>
              <span className='block text-xs font-normal opacity-70'>
                {settings.autoplay.auto_join.enabled ? t('common.enabled') : t('common.disabled')}
              </span>
            </span>
          </Button>
        </div>
        <div className='flex-1 space-y-8 overflow-y-auto p-6'>
          {activePage === 'play' ? (
            <AutoplaySection settings={settings} updateSetting={updateSetting} />
          ) : (
            <AutoJoinSection settings={settings} updateSetting={updateSetting} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
