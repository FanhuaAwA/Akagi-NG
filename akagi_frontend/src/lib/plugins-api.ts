import { fetchJson } from '@/lib/api-client';
import type {
  PluginCatalogEntry,
  PluginInfo,
  PluginInstallResponse,
  PluginUpdateResponse,
} from '@/types';

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

export function fetchPluginCatalogApi(): Promise<PluginCatalogEntry[]> {
  return fetchJson<PluginCatalogEntry[]>('/api/plugins/catalog');
}

export function installRemotePluginApi(
  pluginId: string,
  replace = false,
): Promise<PluginInstallResponse> {
  return fetchJson<PluginInstallResponse>(
    '/api/plugins/install/remote',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: pluginId, replace }),
    },
    'envelope',
  );
}

export function importLocalPluginApi(
  archive: File,
  replace = false,
): Promise<PluginInstallResponse> {
  const form = new FormData();
  form.append('file', archive, archive.name);
  return fetchJson<PluginInstallResponse>(
    `/api/plugins/install/local?replace=${replace ? 'true' : 'false'}`,
    { method: 'POST', body: form },
    'envelope',
  );
}
