import { RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'react-toastify';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { clearHttpCaptures, fetchHttpCaptures, fetchLogTail } from '@/lib/logs-api';
import type { HttpCapture } from '@/types';

interface LogPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function LogPanel({ open, onClose }: LogPanelProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'http' | 'backend'>('http');
  const [captures, setCaptures] = useState<HttpCapture[]>([]);
  const [logs, setLogs] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextCaptures, nextLogs] = await Promise.all([fetchHttpCaptures(), fetchLogTail()]);
      setCaptures(nextCaptures.reverse());
      setLogs(nextLogs);
    } catch (error) {
      toast.error(String(error));
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => window.clearInterval(timer);
  }, [open, refresh]);

  const clearCaptures = async () => {
    await clearHttpCaptures();
    setCaptures([]);
    setSelected(null);
  };

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className='flex h-[90vh] max-h-[90vh] flex-col gap-0 p-0 sm:max-w-6xl'>
        <DialogHeader className='border-border border-b p-6 pb-4'>
          <div className='flex items-start justify-between gap-4 pr-8'>
            <div>
              <DialogTitle>{t('logs_page.title')}</DialogTitle>
              <DialogDescription>{t('logs_page.description')}</DialogDescription>
            </div>
            <div className='flex gap-2'>
              <Button variant='outline' size='sm' onClick={() => void refresh()}>
                <RefreshCw className='mr-2 h-4 w-4' />
                {t('common.refresh')}
              </Button>
              {tab === 'http' && (
                <Button variant='outline' size='sm' onClick={() => void clearCaptures()}>
                  <Trash2 className='mr-2 h-4 w-4' />
                  {t('common.clear')}
                </Button>
              )}
            </div>
          </div>
          <div className='mt-4 flex gap-2'>
            <Button
              variant={tab === 'http' ? 'default' : 'outline'}
              size='sm'
              onClick={() => setTab('http')}
            >
              {t('logs_page.http_tab')} ({captures.length})
            </Button>
            <Button
              variant={tab === 'backend' ? 'default' : 'outline'}
              size='sm'
              onClick={() => setTab('backend')}
            >
              {t('logs_page.backend_tab')}
            </Button>
          </div>
        </DialogHeader>

        {tab === 'backend' ? (
          <pre className='bg-muted/30 flex-1 overflow-auto p-5 font-mono text-xs whitespace-pre-wrap'>
            {logs.join('\n') || t('logs_page.empty')}
          </pre>
        ) : (
          <div className='grid min-h-0 flex-1 grid-cols-[minmax(300px,0.9fr)_minmax(0,1.4fr)]'>
            <div className='border-border overflow-y-auto border-r'>
              {captures.length === 0 && (
                <p className='text-muted-foreground p-6 text-sm'>{t('logs_page.empty_http')}</p>
              )}
              {captures.map((capture) => (
                <button
                  key={capture.id}
                  type='button'
                  onClick={() => setSelected(capture.id)}
                  className={`border-border hover:bg-muted/60 block w-full border-b p-4 text-left transition ${selected === capture.id ? 'bg-muted' : ''}`}
                >
                  <div className='flex items-center gap-2 text-xs font-semibold'>
                    <span className='rounded bg-violet-100 px-1.5 py-0.5 text-violet-700 dark:bg-violet-950 dark:text-violet-300'>
                      {capture.method}
                    </span>
                    <span>{capture.status_code ?? '…'}</span>
                    {capture.telemetry && (
                      <span className='text-amber-600'>{capture.telemetry.category}</span>
                    )}
                    {capture.certificate_rewrite && (
                      <span className='text-emerald-600'>{t('logs_page.ca_rewritten')}</span>
                    )}
                  </div>
                  <p className='mt-2 truncate text-xs'>{capture.url}</p>
                  <p className='text-muted-foreground mt-1 text-[11px]'>
                    {new Date(capture.timestamp * 1000).toLocaleTimeString()}
                  </p>
                </button>
              ))}
            </div>
            <CaptureDetail
              capture={captures.find((capture) => capture.id === selected) ?? captures[0]}
              empty={t('logs_page.select')}
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function CaptureDetail({ capture, empty }: { capture?: HttpCapture; empty: string }) {
  if (!capture) return <div className='text-muted-foreground p-6 text-sm'>{empty}</div>;
  return (
    <div className='min-w-0 overflow-y-auto p-5 text-xs'>
      <h3 className='mb-2 text-sm font-semibold'>
        {capture.method} {capture.url}
      </h3>
      {capture.certificate_rewrite && (
        <div className='mb-4 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-emerald-700 dark:text-emerald-300'>
          CA telemetry corrected: {capture.certificate_rewrite.corrected}; uncorrected:{' '}
          {capture.certificate_rewrite.uncorrected}
        </div>
      )}
      <JsonBlock title='Telemetry' value={capture.telemetry} />
      <JsonBlock title='Request headers' value={capture.request_headers} />
      {capture.request_body && <TextBlock title='Request body' value={capture.request_body} />}
      <JsonBlock title={`Response ${capture.status_code ?? ''}`} value={capture.response_headers} />
      {capture.response_body && <TextBlock title='Response body' value={capture.response_body} />}
      {capture.error && <TextBlock title='Error' value={capture.error} />}
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  if (value == null) return null;
  return <TextBlock title={title} value={JSON.stringify(value, null, 2)} />;
}

function TextBlock({ title, value }: { title: string; value: string }) {
  return (
    <section className='mb-4'>
      <h4 className='text-muted-foreground mb-1 font-semibold uppercase'>{title}</h4>
      <pre className='bg-muted/50 overflow-x-auto rounded-lg p-3 font-mono whitespace-pre-wrap'>
        {value}
      </pre>
    </section>
  );
}
