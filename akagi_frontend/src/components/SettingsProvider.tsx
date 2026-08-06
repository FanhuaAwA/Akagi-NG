import { type ReactNode, useCallback, useEffect, useMemo, useReducer, useRef } from 'react';

import { SETTINGS_DEBOUNCE_MS, TOAST_DURATION_DEFAULT } from '@/config/constants';
import { SettingsContext } from '@/contexts/SettingsContext';
import {
  fetchModelsApi,
  fetchSettingsApi,
  resetSettingsApi,
  saveSettingsApi,
} from '@/hooks/useSettings';
import i18n from '@/i18n/i18n';
import { notify } from '@/lib/notify';
import type { Paths, PathValue, Settings } from '@/types';

// --- 类型与归约器 ---

type SaveStatus = 'saved' | 'saving' | 'error';
type SaveFeedback = 'progress' | 'success' | 'silent';

interface SaveOptions {
  delayMs: number;
  feedback: SaveFeedback;
}

interface State {
  settings: Settings;
  saveStatus: SaveStatus;
  restartRequired: boolean;
  isRestored: boolean;
  // 可用模型列表
  availableModels: string[];
}

type Action =
  | { type: 'INIT_SYNC'; payload: Settings }
  | { type: 'REMOTE_UPDATE'; payload: { locale: string } }
  | { type: 'USER_UPDATE'; path: readonly string[]; value: unknown }
  | {
      type: 'USER_UPDATE_BATCH';
      updates: { path: readonly string[]; value: unknown }[];
    }
  | { type: 'RESTORE_START' }
  | { type: 'RESTORE_SUCCESS'; payload: Settings }
  | { type: 'SET_SAVE_STATUS'; status: SaveStatus }
  | { type: 'SET_RESTART_REQUIRED' }
  | { type: 'SET_AVAILABLE_MODELS'; payload: string[] };

function setByPath(root: Record<string, unknown>, path: readonly string[], value: unknown) {
  let current: Record<string, unknown> = root;
  for (let i = 0; i < path.length - 1; i++) {
    const key = path[i];
    if (typeof current[key] !== 'object' || current[key] === null) {
      current[key] = {};
    }
    current = current[key] as Record<string, unknown>;
  }
  current[path[path.length - 1]] = value;
}

function settingsReducer(state: State, action: Action): State {
  switch (action.type) {
    case 'INIT_SYNC':
      if (JSON.stringify(state.settings) === JSON.stringify(action.payload)) {
        return state;
      }
      return {
        ...state,
        settings: action.payload,
      };

    case 'REMOTE_UPDATE':
      if (state.settings.locale === action.payload.locale) return state;
      return {
        ...state,
        settings: { ...state.settings, locale: action.payload.locale },
      };

    case 'USER_UPDATE': {
      const nextSettings = structuredClone(state.settings) as unknown as Record<string, unknown>;
      setByPath(nextSettings, action.path, action.value);
      return {
        ...state,
        settings: nextSettings as unknown as Settings,
      };
    }

    case 'USER_UPDATE_BATCH': {
      const nextSettings = structuredClone(state.settings) as unknown as Record<string, unknown>;
      action.updates.forEach(({ path, value }) => setByPath(nextSettings, path, value));
      return {
        ...state,
        settings: nextSettings as unknown as Settings,
      };
    }

    case 'RESTORE_SUCCESS':
      return {
        ...state,
        settings: action.payload,
        isRestored: true,
      };

    case 'SET_SAVE_STATUS':
      return { ...state, saveStatus: action.status };

    case 'SET_RESTART_REQUIRED':
      return { ...state, restartRequired: true };

    case 'SET_AVAILABLE_MODELS':
      return { ...state, availableModels: action.payload };

    default:
      return state;
  }
}

// --- 组件 ---

interface SettingsProviderProps {
  children: ReactNode;
  initialSettings: Settings;
  resolvedSettingsPromise?: Promise<Settings>;
}

export function SettingsProvider({
  children,
  initialSettings,
  resolvedSettingsPromise,
}: SettingsProviderProps) {
  const [state, dispatch] = useReducer(settingsReducer, {
    settings: initialSettings,
    saveStatus: 'saved',
    restartRequired: false,
    isRestored: false,
    availableModels: [],
  });

  const { settings, saveStatus, restartRequired, isRestored, availableModels } = state;

  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveSeqRef = useRef(0);

  // --- Effects ---

  // 1. 同步外部属性变更（初次加载/后台刷新）
  useEffect(() => {
    dispatch({ type: 'INIT_SYNC', payload: initialSettings });
  }, [initialSettings]);

  // 2. i18n 同步与广播
  // 保持界面语言与状态一致
  useEffect(() => {
    const targetLocale = settings?.locale;
    if (!targetLocale) return;

    // 本地引擎始终保持同步
    if (i18n.language !== targetLocale) {
      i18n.changeLanguage(targetLocale).catch(console.error);
    }
  }, [settings.locale]);

  // 3. 远程监听（进程间通信）
  useEffect(() => {
    const unsub = window.electron.onLocaleChanged((newLocale: string) => {
      dispatch({ type: 'REMOTE_UPDATE', payload: { locale: newLocale } });
    });
    return () => unsub();
  }, []);

  // 4. 初始模型列表获取
  useEffect(() => {
    let active = true;
    const ready = resolvedSettingsPromise ?? Promise.resolve(initialSettings);
    void ready
      .then((resolvedSettings) => {
        if (active) dispatch({ type: 'INIT_SYNC', payload: resolvedSettings });
        return fetchModelsApi();
      })
      .then((models) => {
        if (active) dispatch({ type: 'SET_AVAILABLE_MODELS', payload: models });
      })
      .catch(console.error);
    return () => {
      active = false;
    };
  }, [initialSettings, resolvedSettingsPromise]);

  // --- 核心方法 ---

  const triggerSave = useCallback((nextSettings: Settings, options: SaveOptions) => {
    const currentSaveId = ++saveSeqRef.current;
    const performSave = async () => {
      dispatch({ type: 'SET_SAVE_STATUS', status: 'saving' });

      // 提示弹窗
      const toastId =
        options.feedback === 'progress' ? notify.loading(i18n.t('settings.status_saving')) : null;

      try {
        const result = await saveSettingsApi(nextSettings);
        if (currentSaveId !== saveSeqRef.current) {
          if (toastId !== null) notify.dismiss(toastId);
          return;
        }
        if (result.restartRequired) dispatch({ type: 'SET_RESTART_REQUIRED' });
        if (result.proxyChanged) {
          try {
            const status = await window.electron.reconcileMihomo(result.gameProxyChanged === true);
            const runtimeError = result.proxyError ?? status.error;
            if (runtimeError) {
              notify.error(runtimeError);
            }
            if (status.gameWindowClosed) {
              notify.info(i18n.t('plugins.game_closed_for_proxy'));
            }
          } catch (proxyError) {
            notify.error(proxyError instanceof Error ? proxyError.message : String(proxyError));
          }
        }
        if (result.desktopChanged) {
          try {
            await window.electron.reconcileDesktop();
          } catch (desktopError) {
            notify.error(
              desktopError instanceof Error ? desktopError.message : String(desktopError),
            );
          }
        }
        if (result.data) dispatch({ type: 'INIT_SYNC', payload: result.data });

        dispatch({ type: 'SET_SAVE_STATUS', status: 'saved' });
        if (toastId !== null) {
          notify.update(toastId, {
            render: i18n.t('settings.status_saved'),
            type: 'success',
            isLoading: false,
            autoClose: 2000,
          });
        } else if (options.feedback === 'success') {
          notify.success(i18n.t('settings.status_saved'), { autoClose: 2000 });
        }
      } catch (e) {
        if (currentSaveId !== saveSeqRef.current) {
          if (toastId !== null) notify.dismiss(toastId);
          return;
        }
        console.error('Save error:', e);
        dispatch({ type: 'SET_SAVE_STATUS', status: 'error' });
        if (toastId !== null) {
          notify.update(toastId, {
            render: i18n.t('settings.status_error'),
            type: 'error',
            isLoading: false,
            autoClose: TOAST_DURATION_DEFAULT,
          });
        } else {
          notify.error(i18n.t('settings.status_error'));
        }
      }
    };

    // 防抖逻辑
    if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    debounceTimerRef.current = setTimeout(performSave, options.delayMs);
  }, []);

  // --- 对外接口 ---

  const refreshSettings = useCallback(async () => {
    try {
      const [data, models] = await Promise.all([fetchSettingsApi(), fetchModelsApi()]);
      dispatch({ type: 'INIT_SYNC', payload: data });
      dispatch({ type: 'SET_AVAILABLE_MODELS', payload: models });
    } catch (e) {
      console.error('Failed to refresh settings or models:', e);
    }
  }, []);

  const restoreDefaults = useCallback(async () => {
    try {
      const data = await resetSettingsApi();
      dispatch({ type: 'RESTORE_SUCCESS', payload: data });
      await window.electron.reconcileDesktop();
    } catch (e) {
      console.error('Restore Defaults error:', e);
    }
  }, []);

  const updateSetting = useCallback(
    <P extends Paths<Settings>>(
      path: readonly [...P],
      value: PathValue<Settings, P>,
      shouldDebounce = false,
    ) => {
      dispatch({ type: 'USER_UPDATE', path, value });

      // 计算变更后的设置用于触发保存
      const nextSettings = structuredClone(settings) as unknown as Record<string, unknown>;
      setByPath(nextSettings, path, value);

      const localePath =
        path.length > 0 && path[0] === 'locale' && value && typeof value === 'string';
      if (localePath) {
        window.electron.updateLocale(value).catch(console.error);
      }

      const autoplayChange = path[0] === 'autoplay';
      const importantAutoplayChange =
        autoplayChange && (path[1] === 'enabled' || path[1] === 'delay_mode');
      const saveOptions: SaveOptions = {
        delayMs: autoplayChange ? (shouldDebounce ? 150 : 0) : SETTINGS_DEBOUNCE_MS,
        feedback: autoplayChange ? (importantAutoplayChange ? 'success' : 'silent') : 'progress',
      };

      triggerSave(nextSettings as unknown as Settings, saveOptions);
    },
    [settings, triggerSave],
  );

  const updateSettingsBatch = useCallback(
    (updates: { path: readonly string[]; value: unknown }[], shouldDebounce = false) => {
      dispatch({ type: 'USER_UPDATE_BATCH', updates });

      // 计算变更后的设置用于触发保存
      const nextSettings = structuredClone(settings) as unknown as Record<string, unknown>;
      let hasLocaleChange = false;
      let newLocale = '';

      updates.forEach(({ path, value }) => {
        setByPath(nextSettings, path, value);
        if (path.length > 0 && path[0] === 'locale' && value && typeof value === 'string') {
          hasLocaleChange = true;
          newLocale = value;
        }
      });

      if (hasLocaleChange) {
        window.electron.updateLocale(newLocale).catch(console.error);
      }

      const autoplayOnly = updates.every(({ path }) => path[0] === 'autoplay');
      const importantAutoplayChange = updates.some(
        ({ path }) => path[0] === 'autoplay' && (path[1] === 'enabled' || path[1] === 'delay_mode'),
      );
      const saveOptions: SaveOptions = {
        delayMs: autoplayOnly ? (shouldDebounce ? 150 : 0) : SETTINGS_DEBOUNCE_MS,
        feedback: autoplayOnly ? (importantAutoplayChange ? 'success' : 'silent') : 'progress',
      };

      triggerSave(nextSettings as unknown as Settings, saveOptions);
    },
    [settings, triggerSave],
  );

  const contextValue = useMemo(
    () => ({
      settings,
      saveStatus,
      restartRequired,
      isRestored,
      updateSetting,
      updateSettingsBatch,
      restoreDefaults,
      refreshSettings,
      availableModels,
    }),
    [
      settings,
      saveStatus,
      restartRequired,
      isRestored,
      updateSetting,
      updateSettingsBatch,
      restoreDefaults,
      refreshSettings,
      availableModels,
    ],
  );

  return <SettingsContext value={contextValue}>{children}</SettingsContext>;
}
