import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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

      <div className='grid grid-cols-2 gap-4'>
        <SettingsItem
          label={t('settings.desktop.overlay_mode')}
          description={t('settings.desktop.overlay_mode_desc')}
        >
          <Select
            value={desktop.overlay_mode}
            onValueChange={(value) =>
              updateSetting(['desktop', 'overlay_mode'], value as 'standard' | 'advanced')
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='standard'>{t('settings.desktop.overlay_standard')}</SelectItem>
              <SelectItem value='advanced'>{t('settings.desktop.overlay_advanced')}</SelectItem>
            </SelectContent>
          </Select>
        </SettingsItem>

        <SettingsItem
          label={t('settings.desktop.advanced_host')}
          description={t('settings.desktop.advanced_host_desc')}
        >
          <Select
            value={desktop.advanced_host}
            disabled={desktop.overlay_mode !== 'advanced'}
            onValueChange={(value) =>
              updateSetting(['desktop', 'advanced_host'], value as 'auto' | 'discord' | 'protected')
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='auto'>{t('settings.desktop.host_auto')}</SelectItem>
              <SelectItem value='discord'>{t('settings.desktop.host_discord')}</SelectItem>
              <SelectItem value='protected'>{t('settings.desktop.host_protected')}</SelectItem>
            </SelectContent>
          </Select>
        </SettingsItem>
      </div>

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
