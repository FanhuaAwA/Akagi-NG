import { useEffect, useMemo, useState } from 'react';

import type {
  FullRecommendationData,
  InferenceStatus,
  NotificationItem,
  SSEErrorCode,
} from '@/types';

interface UseSSEConnectionResult {
  data: FullRecommendationData | null;
  inferenceStatus: InferenceStatus | null;
  notifications: NotificationItem[];
  isConnected: boolean;
  error: SSEErrorCode | string | null;
}

export function useSSEConnection(url: string | null): UseSSEConnectionResult {
  const [data, setData] = useState<FullRecommendationData | null>(null);
  const [inferenceStatus, setInferenceStatus] = useState<InferenceStatus | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<SSEErrorCode | string | null>(null);

  useEffect(() => {
    if (!url) return;

    let es: EventSource;
    try {
      es = new EventSource(url);
    } catch (e) {
      console.error('Invalid SSE URL:', e);
      queueMicrotask(() => {
        setError('config_error');
        setIsConnected(false);
      });
      return;
    }

    const handleOpen = () => {
      setIsConnected(true);
      setError(null);
    };

    const handleRecommendations = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data);
        setData(parsed);
      } catch (e) {
        console.error('Failed to parse recommendations', e);
      }
    };

    const handleNotification = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data);
        if (parsed.list) {
          setNotifications(parsed.list);
        }
      } catch (e) {
        console.error('Failed to parse notification', e);
      }
    };

    const handleInferenceStatus = (event: MessageEvent) => {
      try {
        setInferenceStatus(JSON.parse(event.data) as InferenceStatus);
      } catch (e) {
        console.error('Failed to parse inference status', e);
      }
    };

    const handleError = (event: Event) => {
      console.error('SSE error:', event);
      setIsConnected(false);
      setError('service_disconnected');
      if (es.readyState === EventSource.CLOSED) {
        es.close();
      }
    };

    es.addEventListener('open', handleOpen);
    es.addEventListener('recommendations', handleRecommendations);
    es.addEventListener('notification', handleNotification);
    es.addEventListener('inference_status', handleInferenceStatus);
    es.addEventListener('error', handleError);

    return () => {
      es.removeEventListener('open', handleOpen);
      es.removeEventListener('recommendations', handleRecommendations);
      es.removeEventListener('notification', handleNotification);
      es.removeEventListener('inference_status', handleInferenceStatus);
      es.removeEventListener('error', handleError);
      es.close();
    };
  }, [url]);

  return useMemo(
    () => ({ data, inferenceStatus, notifications, isConnected, error }),
    [data, inferenceStatus, notifications, isConnected, error],
  );
}
