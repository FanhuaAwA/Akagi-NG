import { Clock3, DoorOpen, ShieldCheck } from 'lucide-react';
import { type FC, memo } from 'react';
import { useTranslation } from 'react-i18next';

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
import type { Paths, PathValue, Settings } from '@/types';

interface AutoJoinSectionProps {
  settings: Settings;
  updateSetting: <P extends Paths<Settings>>(
    path: readonly [...P],
    value: PathValue<Settings, P>,
    shouldDebounce?: boolean,
  ) => void;
}

export const AutoJoinSection: FC<AutoJoinSectionProps> = memo(({ settings, updateSetting }) => {
  const { t } = useTranslation();
  const config = settings.autoplay.auto_join;
  const temporarilyDisabled = true;

  return (
    <div className='space-y-6'>
      <div className='border-border/70 bg-muted/20 rounded-xl border p-5'>
        <p className='mb-4 text-sm font-medium text-amber-500'>
          {t('settings.auto_join.temporarily_disabled')}
        </p>
        <SettingsItem
          label={t('settings.auto_join.enabled')}
          description={t('settings.auto_join.enabled_desc')}
        >
          <CapsuleSwitch
            className='w-fit'
            checked={false}
            disabled={temporarilyDisabled}
            onCheckedChange={(value) => updateSetting(['autoplay', 'auto_join', 'enabled'], value)}
            labelOn={t('common.enabled')}
            labelOff={t('common.disabled')}
          />
        </SettingsItem>
      </div>

      <section className='space-y-3'>
        <div className='flex items-center gap-2'>
          <DoorOpen className='h-4 w-4 text-violet-500' />
          <h3 className='settings-section-title'>{t('settings.auto_join.target_title')}</h3>
        </div>
        <p className='text-muted-foreground text-sm'>{t('settings.auto_join.target_desc')}</p>
        <div className='grid grid-cols-1 gap-4 md:grid-cols-2'>
          <SettingsItem
            label={t('settings.auto_join.room')}
            description={t('settings.auto_join.room_desc')}
          >
            <Select
              disabled={temporarilyDisabled}
              value={config.room}
              onValueChange={(value: Settings['autoplay']['auto_join']['room']) =>
                updateSetting(['autoplay', 'auto_join', 'room'], value)
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='copper'>{t('settings.auto_join.room_copper')}</SelectItem>
                <SelectItem value='silver'>{t('settings.auto_join.room_silver')}</SelectItem>
                <SelectItem value='gold'>{t('settings.auto_join.room_gold')}</SelectItem>
                <SelectItem value='jade'>{t('settings.auto_join.room_jade')}</SelectItem>
                <SelectItem value='throne'>{t('settings.auto_join.room_throne')}</SelectItem>
              </SelectContent>
            </Select>
          </SettingsItem>

          <SettingsItem
            label={t('settings.auto_join.mode')}
            description={t('settings.auto_join.mode_desc')}
          >
            <Select
              disabled={temporarilyDisabled}
              value={config.mode}
              onValueChange={(value: Settings['autoplay']['auto_join']['mode']) =>
                updateSetting(['autoplay', 'auto_join', 'mode'], value)
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='four_east'>{t('settings.auto_join.mode_four_east')}</SelectItem>
                <SelectItem value='four_south'>
                  {t('settings.auto_join.mode_four_south')}
                </SelectItem>
                <SelectItem value='three_east'>
                  {t('settings.auto_join.mode_three_east')}
                </SelectItem>
                <SelectItem value='three_south'>
                  {t('settings.auto_join.mode_three_south')}
                </SelectItem>
              </SelectContent>
            </Select>
          </SettingsItem>
        </div>
      </section>

      <section className='space-y-3'>
        <div className='flex items-center gap-2'>
          <Clock3 className='h-4 w-4 text-violet-500' />
          <h3 className='settings-section-title'>{t('settings.auto_join.timing_title')}</h3>
        </div>
        <SettingsItem
          label={t('settings.auto_join.result_delay')}
          description={t('settings.auto_join.result_delay_desc')}
        >
          <Input
            disabled={temporarilyDisabled}
            type='number'
            min='5'
            max='60'
            step='0.5'
            value={config.result_delay}
            onChange={(event) => {
              const next = Number.parseFloat(event.target.value);
              if (!Number.isNaN(next)) {
                updateSetting(['autoplay', 'auto_join', 'result_delay'], next, true);
              }
            }}
          />
        </SettingsItem>
      </section>

      <div className='border-border/70 bg-muted/20 flex gap-3 rounded-xl border p-4'>
        <ShieldCheck className='mt-0.5 h-5 w-5 shrink-0 text-emerald-500' />
        <p className='text-muted-foreground text-sm leading-6'>{t('settings.auto_join.safety')}</p>
      </div>
    </div>
  );
});

AutoJoinSection.displayName = 'AutoJoinSection';
