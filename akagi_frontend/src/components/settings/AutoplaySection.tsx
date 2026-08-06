import { Clock3, MousePointer2, SlidersHorizontal } from 'lucide-react';
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
import { Slider } from '@/components/ui/slider';
import type { DelayRange, Paths, PathValue, Settings } from '@/types';

interface AutoplaySectionProps {
  settings: Settings;
  updateSetting: <P extends Paths<Settings>>(
    path: readonly [...P],
    value: PathValue<Settings, P>,
    shouldDebounce?: boolean,
  ) => void;
}

interface TimingRangeSliderProps {
  label: string;
  description: string;
  value: DelayRange;
  onChange: (value: DelayRange) => void;
}

const ADVANCED_ACTIONS: Array<keyof Settings['autoplay']['advanced_timing']> = [
  'first_discard',
  'discard',
  'tsumogiri',
  'reach',
  'reach_discard',
  'chi',
  'pon',
  'daiminkan',
  'ankan',
  'kakan',
  'ron',
  'tsumo',
  'ryukyoku',
  'nukidora',
  'skip',
  'candidate',
];

function TimingRangeSlider({ label, description, value, onChange }: TimingRangeSliderProps) {
  return (
    <div className='border-border/70 bg-muted/15 space-y-3 rounded-xl border p-4'>
      <div className='flex items-start justify-between gap-3'>
        <div>
          <p className='text-sm font-medium'>{label}</p>
          <p className='text-muted-foreground mt-1 text-xs leading-5'>{description}</p>
        </div>
        <span className='bg-background border-border shrink-0 rounded-md border px-2 py-1 text-xs font-medium tabular-nums'>
          {value.min.toFixed(1)}s — {value.max.toFixed(1)}s
        </span>
      </div>
      <Slider
        min={0}
        max={15}
        step={0.1}
        minStepsBetweenThumbs={0}
        value={[value.min, value.max]}
        onValueChange={(next) => {
          if (next.length >= 2) onChange({ min: next[0], max: next[1] });
        }}
      />
      <div className='text-muted-foreground flex justify-between text-[10px] tabular-nums'>
        <span>0s</span>
        <span>15s</span>
      </div>
    </div>
  );
}

export const AutoplaySection: FC<AutoplaySectionProps> = memo(({ settings, updateSetting }) => {
  const { t } = useTranslation();
  const autoplay = settings.autoplay;

  const updateInputNumber = (
    key: keyof Settings['autoplay']['input'],
    rawValue: string,
    integer = false,
  ) => {
    const next = integer ? Number.parseInt(rawValue, 10) : Number.parseFloat(rawValue);
    if (!Number.isNaN(next)) {
      updateSetting(['autoplay', 'input'], { ...autoplay.input, [key]: next }, true);
    }
  };

  return (
    <div className='space-y-7'>
      <div className='border-border/70 bg-muted/20 rounded-xl border p-5'>
        <SettingsItem
          label={t('settings.autoplay.enabled')}
          description={t('settings.autoplay.enabled_desc')}
        >
          <CapsuleSwitch
            className='w-fit'
            checked={autoplay.enabled}
            onCheckedChange={(value) => updateSetting(['autoplay', 'enabled'], value)}
            labelOn={t('common.enabled')}
            labelOff={t('common.disabled')}
          />
        </SettingsItem>
      </div>

      <SettingsItem
        label={t('settings.autoplay.delay_mode')}
        description={t('settings.autoplay.delay_mode_desc')}
      >
        <Select
          value={autoplay.delay_mode}
          onValueChange={(value: Settings['autoplay']['delay_mode']) =>
            updateSetting(['autoplay', 'delay_mode'], value)
          }
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='human'>{t('settings.autoplay.delay_mode_human')}</SelectItem>
            <SelectItem value='advanced'>{t('settings.autoplay.delay_mode_advanced')}</SelectItem>
          </SelectContent>
        </Select>
      </SettingsItem>

      <section className='space-y-3'>
        <div className='flex items-center gap-2'>
          {autoplay.delay_mode === 'advanced' ? (
            <SlidersHorizontal className='h-4 w-4 text-violet-500' />
          ) : (
            <Clock3 className='h-4 w-4 text-violet-500' />
          )}
          <h3 className='settings-section-title'>
            {t(
              autoplay.delay_mode === 'advanced'
                ? 'settings.autoplay.advanced_title'
                : 'settings.autoplay.builtin_title',
            )}
          </h3>
        </div>
        <p className='text-muted-foreground text-sm'>
          {t(
            autoplay.delay_mode === 'advanced'
              ? 'settings.autoplay.advanced_desc'
              : 'settings.autoplay.builtin_desc',
          )}
        </p>

        {autoplay.delay_mode === 'human' ? (
          <div className='grid grid-cols-1 gap-4 md:grid-cols-2'>
            {(['first_discard', 'discard', 'button', 'candidate'] as const).map((key) => (
              <TimingRangeSlider
                key={key}
                label={t(`settings.autoplay.range_${key}`)}
                description={t(`settings.autoplay.range_${key}_desc`)}
                value={autoplay.timing[key]}
                onChange={(range) =>
                  updateSetting(['autoplay', 'timing'], { ...autoplay.timing, [key]: range }, true)
                }
              />
            ))}
          </div>
        ) : (
          <div className='grid grid-cols-1 gap-4 md:grid-cols-2'>
            {ADVANCED_ACTIONS.map((key) => (
              <TimingRangeSlider
                key={key}
                label={t(`settings.autoplay.action_${key}`)}
                description={t(`settings.autoplay.action_${key}_desc`)}
                value={autoplay.advanced_timing[key]}
                onChange={(range) =>
                  updateSetting(
                    ['autoplay', 'advanced_timing'],
                    { ...autoplay.advanced_timing, [key]: range },
                    true,
                  )
                }
              />
            ))}
          </div>
        )}

        <p className='text-muted-foreground text-xs leading-5'>
          {t('settings.autoplay.timing_safety')}
        </p>
      </section>

      <section className='space-y-4'>
        <div className='flex items-center gap-2'>
          <MousePointer2 className='h-4 w-4 text-violet-500' />
          <h3 className='settings-section-title'>{t('settings.autoplay.input_title')}</h3>
        </div>

        <SettingsItem
          label={t('settings.autoplay.window_keyword')}
          description={t('settings.autoplay.window_keyword_desc')}
        >
          <Input
            value={autoplay.window_keyword}
            placeholder='majsoul, jantama'
            onChange={(event) =>
              updateSetting(['autoplay', 'window_keyword'], event.target.value, true)
            }
          />
        </SettingsItem>

        <div className='grid grid-cols-1 gap-4 md:grid-cols-2'>
          <SettingsItem
            label={t('settings.autoplay.bezier_smoothing')}
            description={t('settings.autoplay.bezier_smoothing_desc')}
          >
            <Input
              type='number'
              min='0'
              max='1'
              step='0.05'
              value={autoplay.input.bezier_smoothing}
              onChange={(event) => updateInputNumber('bezier_smoothing', event.target.value)}
            />
          </SettingsItem>

          <SettingsItem
            label={t('settings.autoplay.bezier_steps')}
            description={t('settings.autoplay.bezier_steps_desc')}
          >
            <Input
              type='number'
              min='10'
              step='1'
              value={autoplay.input.bezier_steps}
              onChange={(event) => updateInputNumber('bezier_steps', event.target.value, true)}
            />
          </SettingsItem>
        </div>
      </section>
    </div>
  );
});

AutoplaySection.displayName = 'AutoplaySection';
