import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { OT3ServicePanel } from '@/components/settings/OT3ServicePanel';
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
import { Slider } from '@/components/ui/slider';
import { useSettings } from '@/hooks/useSettings';
import type { Paths, PathValue, Settings } from '@/types';

interface ModelConfigSectionProps {
  settings: Settings;
  updateSetting: <P extends Paths<Settings>>(
    path: readonly [...P],
    value: PathValue<Settings, P>,
    shouldDebounce?: boolean,
  ) => void;
  updateSettingsBatch: (
    updates: { path: readonly string[]; value: unknown }[],
    shouldDebounce?: boolean,
  ) => void;
}

export function ModelConfigSection({
  settings,
  updateSetting,
  updateSettingsBatch,
}: ModelConfigSectionProps) {
  const { t } = useTranslation();
  const { availableModels } = useSettings();
  const [tempInput, setTempInput] = useState(settings.model_config.temperature.toString());
  const [isEditingTemp, setIsEditingTemp] = useState(false);
  const displayTemp = isEditingTemp ? tempInput : settings.model_config.temperature.toString();
  const isFlyA = settings.ot.provider === 'flya_test_api';
  const temperatureDisabled = settings.ot.online && isFlyA;
  const activeServer = isFlyA ? settings.ot.flya_server : settings.ot.server;
  const activeKey = isFlyA ? settings.ot.flya_api_key : settings.ot.api_key;
  const keyConfigured = isFlyA ? settings.ot.flya_api_key_configured : Boolean(settings.ot.api_key);

  return (
    <div className='space-y-4'>
      <h3 className='settings-section-title'>{t('settings.model_config.title')}</h3>

      <div className='grid grid-cols-2 gap-6'>
        {/* Left Column: Engine & Model Selection */}
        <div className='space-y-4'>
          <SettingsItem label={t('settings.model_config.mode_selection')}>
            <CapsuleSwitch
              checked={settings.ot.online}
              onCheckedChange={(val) => {
                updateSettingsBatch([
                  { path: ['ot', 'online'], value: val },
                  ...(val ? [{ path: ['ot', 'protocol'], value: 'v3' }] : []),
                ]);
              }}
              labelOn={t('settings.model_config.online_mode')}
              labelOff={t('settings.model_config.local_mode')}
            />
          </SettingsItem>

          {settings.ot.online ? (
            <>
              <SettingsItem label={t('settings.model_config.service_type')}>
                <Select
                  value={settings.ot.provider}
                  onValueChange={(value: 'akagi_ot3' | 'flya_test_api') =>
                    updateSettingsBatch([
                      { path: ['ot', 'provider'], value },
                      ...(value === 'akagi_ot3' ? [{ path: ['ot', 'protocol'], value: 'v3' }] : []),
                    ])
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='akagi_ot3'>Akagi OT3</SelectItem>
                    <SelectItem value='flya_test_api'>FlyA-test-api</SelectItem>
                  </SelectContent>
                </Select>
              </SettingsItem>
              <SettingsItem label={t('settings.model_config.server_url')}>
                <Input
                  className={
                    !activeServer ? 'border-destructive focus-visible:ring-destructive' : ''
                  }
                  value={activeServer}
                  onChange={(event) =>
                    updateSettingsBatch([
                      {
                        path: ['ot', isFlyA ? 'flya_server' : 'server'],
                        value: event.target.value,
                      },
                    ])
                  }
                  placeholder='https://api.example.com'
                />
              </SettingsItem>
              <SettingsItem label={t('settings.model_config.api_key')}>
                <Input
                  type='password'
                  className={
                    !keyConfigured ? 'border-destructive focus-visible:ring-destructive' : ''
                  }
                  value={activeKey}
                  onChange={(event) =>
                    updateSettingsBatch([
                      {
                        path: ['ot', isFlyA ? 'flya_api_key' : 'api_key'],
                        value: event.target.value,
                      },
                      ...(isFlyA ? [{ path: ['ot', 'flya_api_key_configured'], value: true }] : []),
                    ])
                  }
                  placeholder={
                    isFlyA && settings.ot.flya_api_key_last4
                      ? `••••${settings.ot.flya_api_key_last4}`
                      : '<YOUR_API_KEY>'
                  }
                />
              </SettingsItem>
              <SettingsItem
                label={t('settings.model_config.online_proxy')}
                description={t('settings.model_config.online_proxy_desc')}
              >
                <div className='flex w-full items-center gap-3'>
                  <CapsuleSwitch
                    checked={settings.ot.proxy_enabled}
                    onCheckedChange={(val) => updateSetting(['ot', 'proxy_enabled'], val)}
                    labelOn={t('common.enabled')}
                    labelOff={t('common.disabled')}
                  />
                  <Input
                    className='flex-1'
                    disabled={!settings.ot.proxy_enabled}
                    value={settings.ot.proxy}
                    onChange={(e) => updateSetting(['ot', 'proxy'], e.target.value)}
                    placeholder='socks5h://127.0.0.1:7890'
                  />
                </div>
              </SettingsItem>
            </>
          ) : (
            <>
              <SettingsItem label={t('settings.model_config.model_4p')}>
                <Select
                  value={settings.model_config.model_4p}
                  onValueChange={(val) => updateSetting(['model_config', 'model_4p'], val)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder='Select 4P Model' />
                  </SelectTrigger>
                  <SelectContent>
                    {availableModels.length > 0 ? (
                      availableModels.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))
                    ) : (
                      <SelectItem value='none' disabled>
                        {t('settings.model_config.no_models_found')}
                      </SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </SettingsItem>

              <SettingsItem label={t('settings.model_config.model_3p')}>
                <Select
                  value={settings.model_config.model_3p}
                  onValueChange={(val) => updateSetting(['model_config', 'model_3p'], val)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder='Select 3P Model' />
                  </SelectTrigger>
                  <SelectContent>
                    {availableModels.length > 0 ? (
                      availableModels.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))
                    ) : (
                      <SelectItem value='none' disabled>
                        {t('settings.model_config.no_models_found')}
                      </SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </SettingsItem>
            </>
          )}
        </div>

        {/* Right Column: Shared Model Parameters */}
        <div className='space-y-4'>
          <SettingsItem
            label={t('settings.model_config.temperature')}
            description={
              temperatureDisabled
                ? t('settings.model_config.temperature_flya_disabled')
                : t('settings.model_config.temperature_desc')
            }
          >
            <div
              className={`flex items-center gap-4 pt-1 ${temperatureDisabled ? 'opacity-50' : ''}`}
            >
              <Slider
                disabled={temperatureDisabled}
                min={0}
                max={100}
                step={0.1}
                value={[
                  100 *
                    (Math.log(Math.max(0.1, settings.model_config.temperature) / 0.1) /
                      Math.log(13)),
                ]}
                markers={[100 * (Math.log(0.3 / 0.1) / Math.log(13))]}
                onValueChange={(val) => {
                  const temp = 0.1 * Math.pow(13, val[0] / 100);
                  const rounded = Math.round(temp * 1000) / 1000;
                  updateSetting(['model_config', 'temperature'], rounded, true);
                }}
                className='flex-1'
              />
              <Input
                disabled={temperatureDisabled}
                className='w-16 text-center tabular-nums'
                value={displayTemp}
                onFocus={() => {
                  setIsEditingTemp(true);
                  setTempInput(settings.model_config.temperature.toString());
                }}
                onChange={(e) => setTempInput(e.target.value)}
                onBlur={() => {
                  setIsEditingTemp(false);
                  let val = parseFloat(tempInput);
                  if (isNaN(val)) {
                    return;
                  }
                  val = Math.max(0.1, Math.min(1.3, val));
                  updateSetting(['model_config', 'temperature'], val, true);
                  setTempInput(val.toString());
                }}
              />
            </div>
          </SettingsItem>
        </div>
      </div>

      {settings.ot.online && (
        <OT3ServicePanel
          key={settings.ot.provider}
          settings={settings}
          updateSettingsBatch={updateSettingsBatch}
        />
      )}
    </div>
  );
}
