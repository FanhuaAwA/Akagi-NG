import { CheckCircle2, Clock3, LoaderCircle, TriangleAlert } from 'lucide-react';
import { use, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { GameContext } from '@/contexts/GameContext';
import { cn } from '@/lib/utils';

const PHASE_STYLES = {
  waiting:
    'border-slate-300/70 bg-white/80 text-slate-600 dark:border-slate-700 dark:bg-slate-950/80 dark:text-slate-300',
  requesting:
    'border-violet-300/80 bg-violet-50/90 text-violet-700 shadow-violet-500/10 dark:border-violet-700 dark:bg-violet-950/85 dark:text-violet-300',
  success:
    'border-emerald-300/80 bg-emerald-50/90 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/85 dark:text-emerald-300',
  error:
    'border-rose-300/80 bg-rose-50/90 text-rose-700 dark:border-rose-800 dark:bg-rose-950/85 dark:text-rose-300',
} as const;

function formatElapsed(milliseconds: number): string {
  const safeMilliseconds = Math.max(0, Math.round(milliseconds));
  if (safeMilliseconds < 1000) return `${safeMilliseconds} ms`;
  if (safeMilliseconds < 10_000) return `${(safeMilliseconds / 1000).toFixed(1)} s`;
  return `${Math.round(safeMilliseconds / 1000)} s`;
}

export function InferenceStatusIndicator({ className }: { className?: string }) {
  const { t } = useTranslation();
  const context = use(GameContext);
  const status = context?.inferenceStatus ?? null;
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!status || status.phase !== 'requesting') return;
    const timer = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(timer);
  }, [status]);

  const elapsed =
    status?.phase === 'requesting'
      ? Math.max(status.elapsed_ms, now - status.started_at_ms)
      : (status?.elapsed_ms ?? 0);

  const phase = status?.phase ?? 'waiting';
  const label = t(`inference_status.${phase}`);
  const provider = status?.provider || t('inference_status.online');
  const detail = status?.model ? `${provider} / ${status.model}` : provider;
  const showElapsed = Boolean(status);

  const Icon =
    phase === 'requesting'
      ? LoaderCircle
      : phase === 'success'
        ? CheckCircle2
        : phase === 'error'
          ? TriangleAlert
          : Clock3;

  return (
    <div
      className={cn(
        'no-drag z-indicator inline-flex h-7 max-w-72 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium shadow-sm backdrop-blur-md transition-colors',
        PHASE_STYLES[phase],
        className,
      )}
      title={`${detail} · ${label}${showElapsed ? ` · ${formatElapsed(elapsed)}` : ''}${status ? ` · ${status.request_id}` : ''}`}
      aria-live='polite'
      aria-label={`${detail} ${label}${showElapsed ? ` ${formatElapsed(elapsed)}` : ''}`}
    >
      <Icon className={cn('h-3.5 w-3.5 shrink-0', phase === 'requesting' && 'animate-spin')} />
      <span className='max-w-28 truncate'>{provider}</span>
      <span aria-hidden='true'>·</span>
      <span className='whitespace-nowrap'>{label}</span>
      {showElapsed && (
        <>
          <span aria-hidden='true'>·</span>
          <span className='font-mono whitespace-nowrap tabular-nums'>{formatElapsed(elapsed)}</span>
        </>
      )}
    </div>
  );
}
