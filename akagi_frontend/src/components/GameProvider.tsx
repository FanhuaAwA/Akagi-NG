import { type ReactNode, useMemo, useState } from 'react';

import { GameContext } from '@/contexts/GameContext';
import { useConnectionConfig } from '@/hooks/useConnectionConfig';
import { useSSEConnection } from '@/hooks/useSSEConnection';
import { useStatusNotification } from '@/hooks/useStatusNotification';

export function GameProvider({
  children,
  backendReady = true,
}: {
  children: ReactNode;
  backendReady?: boolean;
}) {
  const { backendUrl } = useConnectionConfig();
  const { data, inferenceStatus, notifications, isConnected, error } = useSSEConnection(
    backendReady ? backendUrl : null,
  );
  const { statusMessage, statusType } = useStatusNotification(notifications, error);
  const [isHudActive, setIsHudActive] = useState(window.location.hash === '#/hud');

  const value = useMemo(
    () => ({
      data,
      inferenceStatus,
      notifications,
      isConnected,
      error,
      statusMessage,
      statusType,
      isHudActive,
      setIsHudActive,
    }),
    [
      data,
      inferenceStatus,
      notifications,
      isConnected,
      error,
      statusMessage,
      statusType,
      isHudActive,
    ],
  );

  return <GameContext value={value}>{children}</GameContext>;
}
