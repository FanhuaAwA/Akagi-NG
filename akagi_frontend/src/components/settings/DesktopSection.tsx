import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import { SettingsItem } from '@/components/ui/settings-item';
import type { Paths, PathValue, Settings } from '@/types';

interface DesktopSectionProps {
  settings: Settings;
  updateSetting: <P extends Paths<Settings>>(
    path: readonly [...P],
    value: PathValue<Settings, P>,
    shouldDebounce?: boolean,
  ) => void;
}

export function DesktopSection({ settings, updateSetting }: DesktopSectionProps) {
  const { t } = useTranslation();
  const desktop = settings.desktop;

  return (
    <div className='space-y-4'>
      <h3 className='settings-section-title'>{t('settings.desktop.title')}</h3>

      <SettingsItem
        layout='row'
        label={t('settings.desktop.capture_protection')}
        description={t('settings.desktop.capture_protection_desc')}
      >
        <Checkbox
          checked={desktop.capture_protection}
          onCheckedChange={(value) =>
            updateSetting(['desktop', 'capture_protection'], value === true)
          }
        />
      </SettingsItem>

      <SettingsItem layout='row' label={t('settings.desktop.tray_visible')}>
        <Checkbox
          checked={desktop.tray_visible}
          onCheckedChange={(value) => updateSetting(['desktop', 'tray_visible'], value === true)}
        />
      </SettingsItem>

      <Alert variant='warning'>
        <AlertTriangle className='h-4 w-4' />
        <AlertDescription>{t('settings.desktop.capture_limit_notice')}</AlertDescription>
      </Alert>
    </div>
  );
}
