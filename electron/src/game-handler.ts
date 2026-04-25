import type { WebContents } from 'electron';

import { createLogger } from './logger.js';

const logger = createLogger('GameHandler');

export interface WebSocketCreatedEvent {
  requestId: string;
  url: string;
}
export interface WebSocketClosedEvent {
  requestId: string;
}
export interface WebSocketFrameReceivedEvent {
  requestId: string;
  response?: {
    payloadData?: string;
    opcode?: number;
  };
}
export interface WebSocketFrameSentEvent {
  requestId: string;
  response?: {
    payloadData?: string;
    opcode?: number;
  };
}
export interface ResponseReceivedEvent {
  response: {
    url: string;
  };
}

export type BackendIngestPayload =
  | {
      source: 'electron';
      type: 'debugger_detached';
      reason: string;
      time: number;
    }
  | {
      source: 'electron';
      type: 'websocket_created';
      requestId: string;
      url: string;
      time: number;
    }
  | {
      source: 'electron';
      type: 'websocket_closed';
      requestId: string;
      time: number;
    }
  | {
      source: 'electron';
      type: 'websocket';
      requestId: string;
      direction: 'inbound' | 'outbound';
      data: string;
      opcode: number;
      time: number;
    }
  | {
      source: 'electron';
      type: 'liqi_definition';
      data: string;
      url: string;
    };

export class GameHandler {
  private attached = false;
  private readonly BACKEND_API: string;

  constructor(
    private webContents: WebContents,
    apiBase: string,
  ) {
    this.BACKEND_API = `${apiBase}/api/ingest`;
  }

  public async attach() {
    if (this.attached || this.webContents.isDestroyed()) return;

    try {
      // 1. Listen for process issues
      this.webContents.on('render-process-gone', (_event, details) => {
        logger.error(`Renderer process gone: ${details.reason} (${details.exitCode})`);
        this.attached = false;
      });

      this.webContents.on('did-start-navigation', (_event, url, isInPlace, isMainFrame) => {
        if (isMainFrame && !isInPlace) {
          logger.info(`Main frame navigating to: ${url}`);
        }
      });

      // 2. Auto re-attach when page reloads or navigates
      this.webContents.on('did-finish-load', async () => {
        if (!this.attached && !this.webContents.isDestroyed()) {
          setTimeout(() => this.tryAttach(), 500);
        }
      });

      // 3. Initial attachment
      await this.tryAttach();
    } catch (err) {
      logger.error('Failed to initialize GameHandler:', err);
    }
  }

  private async tryAttach() {
    if (this.attached || this.webContents.isDestroyed()) return;

    try {
      if (this.webContents.debugger.isAttached()) {
        this.attached = true;
        return;
      }

      this.webContents.debugger.attach('1.3');
      this.attached = true;

      this.webContents.debugger.removeAllListeners('detach');
      this.webContents.debugger.removeAllListeners('message');

      this.webContents.debugger.on('detach', (_event, reason) => {
        logger.warn('Web debugger detached:', reason);
        this.attached = false;

        // If it was a target-closed (e.g. process swap), we don't send to backend yet,
        // just let did-finish-load or other events trigger re-attach.
        if (reason !== 'target_closed') {
          this.sendToBackend({
            source: 'electron',
            type: 'debugger_detached',
            reason: reason,
            time: Date.now() / 1000,
          });
        }
      });

      this.webContents.debugger.on('message', this.handleDebuggerMessage.bind(this));

      // Wrap command in try-catch to avoid crashing if target closes mid-flight
      try {
        await this.webContents.debugger.sendCommand('Network.enable');
      } catch (cmdErr) {
        logger.warn('Failed to enable network inspection for game window:', cmdErr);
      }
    } catch (e) {
      const error = e as Error;
      logger.error('Failed to attach web debugger to game window:', error.message);
      this.attached = false;
    }
  }

  public detach() {
    if (this.attached) {
      this.attached = false;
      if (!this.webContents.isDestroyed()) {
        try {
          this.webContents.debugger.detach();
        } catch {
          // Ignore detach errors if target is already closed
        }
      }
    }
  }

  private async handleDebuggerMessage(_event: unknown, method: string, params: unknown) {
    if (method === 'Network.webSocketCreated') {
      const p = params as WebSocketCreatedEvent;
      this.sendToBackend({
        source: 'electron',
        type: 'websocket_created',
        requestId: p.requestId,
        url: p.url,
        time: Date.now() / 1000,
      });
    } else if (method === 'Network.webSocketClosed') {
      const p = params as WebSocketClosedEvent;
      this.sendToBackend({
        source: 'electron',
        type: 'websocket_closed',
        requestId: p.requestId,
        time: Date.now() / 1000,
      });
    } else if (method === 'Network.webSocketFrameReceived') {
      this.handleWebSocketFrame(params as WebSocketFrameReceivedEvent, 'inbound');
    } else if (method === 'Network.webSocketFrameSent') {
      this.handleWebSocketFrame(params as WebSocketFrameSentEvent, 'outbound');
    } else if (method === 'Network.responseReceived') {
      await this.handleResponseReceived(params as ResponseReceivedEvent);
    }
  }

  private handleWebSocketFrame(
    params: WebSocketFrameReceivedEvent | WebSocketFrameSentEvent,
    direction: 'inbound' | 'outbound',
  ) {
    const { requestId, response } = params;

    if (!response || !response.payloadData) {
      return;
    }

    const payload: BackendIngestPayload = {
      source: 'electron',
      type: 'websocket',
      requestId: requestId,
      direction: direction,
      data: response.payloadData, // Base64 string
      opcode: response.opcode ?? -1,
      time: Date.now() / 1000,
    };

    this.sendToBackend(payload);
  }

  private async handleResponseReceived(params: ResponseReceivedEvent) {
    const { response } = params;

    if (response.url && response.url.includes('liqi.json')) {
      try {
        // Use fetch instead of CDP getResponseBody to avoid "No resource with given identifier" errors
        const res = await fetch(response.url);
        if (res.ok) {
          const text = await res.text();
          this.sendToBackend({
            source: 'electron',
            type: 'liqi_definition',
            data: text, // Send raw text (or JSON string)
            url: response.url,
          });
        } else {
          logger.error(`Failed to fetch liqi.json: HTTP ${res.status}`);
        }
      } catch (e) {
        logger.error('Failed to fetch liqi.json manually:', e);
      }
    }
  }

  private sendToBackend(data: BackendIngestPayload) {
    fetch(this.BACKEND_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }).catch((err) => {
      logger.error(
        'Failed to forward game event to backend:',
        err instanceof Error ? err.message : String(err),
      );
    });
  }
}
