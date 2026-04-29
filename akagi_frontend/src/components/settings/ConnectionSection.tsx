import { Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { CapsuleSwitch } from '@/components/ui/capsule-switch';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { SettingsItem } from '@/components/ui/settings-item';
import { MAJSOUL_SERVERS, type MajsoulServer } from '@/config/platforms';
import type { Paths, PathValue, Settings } from '@/types';

interface ConnectionSectionProps {
  settings: Settings;
  updateSetting: <P extends Paths<Settings>>(
    path: readonly [...P],
    value: PathValue<Settings, P>,
    shouldDebounce?: boolean,
  ) => void;
}

export function ConnectionSection({ settings, updateSetting }: ConnectionSectionProps) {
  const { t } = useTranslation();

  return (
    <div className='space-y-4'>
      <h3 className='settings-section-title'>{t('settings.connection.title')}</h3>

      <SettingsItem label={t('settings.connection.mode')}>
        <CapsuleSwitch
          className='w-fit max-w-full'
          checked={settings.mitm.enabled}
          onCheckedChange={(val) => {
            updateSetting(['mitm', 'enabled'], val);
          }}
          labelOn={t('settings.connection.mode_mitm')}
          labelOff={t('settings.connection.mode_browser')}
        />
      </SettingsItem>

      {!settings.mitm.enabled && (
        <>
          {settings.platform === 'majsoul' && (
            <SettingsItem label={t('settings.connection.majsoul_server')}>
              <Select
                value={settings.majsoul_server || MAJSOUL_SERVERS.CN}
                onValueChange={(val) => updateSetting(['majsoul_server'], val as MajsoulServer)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={MAJSOUL_SERVERS.CN}>
                    {t('settings.connection.majsoul_servers.cn')}
                  </SelectItem>
                  <SelectItem value={MAJSOUL_SERVERS.JP}>
                    {t('settings.connection.majsoul_servers.jp')}
                  </SelectItem>
                  <SelectItem value={MAJSOUL_SERVERS.EN}>
                    {t('settings.connection.majsoul_servers.en')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </SettingsItem>
          )}
          {['riichi_city', 'amatsuki'].includes(settings.platform) && (
            <Alert variant='info'>
              <Info className='h-4 w-4' />
              <AlertDescription className='text-sm'>
                {t('settings.connection.mitm_required_notice')}
              </AlertDescription>
            </Alert>
          )}
        </>
      )}

      {settings.mitm.enabled && (
        <>
          <SettingsItem label={t('settings.connection.mitm.host')}>
            <Input
              value={settings.mitm.host}
              onChange={(e) => updateSetting(['mitm', 'host'], e.target.value)}
            />
          </SettingsItem>
          <SettingsItem label={t('settings.connection.mitm.port')}>
            <Input
              type='number'
              className={
                settings.mitm.port === settings.server.port
                  ? 'border-destructive focus-visible:ring-destructive'
                  : ''
              }
              value={settings.mitm.port}
              onChange={(e) => updateSetting(['mitm', 'port'], parseInt(e.target.value) || 0)}
            />
          </SettingsItem>
          <SettingsItem label={t('settings.connection.mitm.upstream')}>
            <Input
              value={settings.mitm.upstream}
              placeholder='http://127.0.0.1:7890'
              onChange={(e) => updateSetting(['mitm', 'upstream'], e.target.value)}
            />
          </SettingsItem>
        </>
      )}
    </div>
  );
}
