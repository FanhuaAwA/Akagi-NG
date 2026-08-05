import { fetchJson } from '@/lib/api-client';
import type { PluginInfo, PluginUpdateResponse } from '@/types';

export function fetchPluginsApi(): Promise<PluginInfo[]> {
  return fetchJson<PluginInfo[]>('/api/plugins');
}

export function setPluginEnabledApi(
  pluginId: string,
  enabled: boolean,
): Promise<PluginUpdateResponse> {
  return fetchJson<PluginUpdateResponse>(
    `/api/plugins/${encodeURIComponent(pluginId)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    },
    'envelope',
  );
}
