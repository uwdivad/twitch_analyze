import React from 'react';

import { API_BASE } from '../api/client';
import type { ChatMessage, LiveEnvelope, SocketState } from '../types';
import { compactChatMessage } from '../utils/messages';

function streamUrl(): string {
  return `${API_BASE}/api/messages/stream`;
}

type UseLiveMessagesArgs = {
  activeChannel: string;
  enabled: boolean;
  onMessage: (message: ChatMessage) => void;
};

const INITIAL_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30_000;

export function useLiveMessages({ activeChannel, enabled, onMessage }: UseLiveMessagesArgs): SocketState {
  const [socketState, setSocketState] = React.useState<SocketState>('connecting');

  React.useEffect(() => {
    if (!enabled) {
      setSocketState('offline');
      return;
    }

    let disposed = false;
    let source: EventSource | null = null;
    let reconnectTimer: number | null = null;
    let reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;

    const connect = () => {
      if (disposed) {
        return;
      }
      setSocketState('connecting');
      source = new EventSource(streamUrl());

      source.onopen = () => {
        reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
        setSocketState('live');
      };
      source.onerror = () => {
        if (!source) {
          return;
        }
        if (source.readyState === EventSource.CLOSED) {
          // Fatal close (e.g. an HTTP-level failure): EventSource will never
          // retry on its own, so schedule a manual reconnect with backoff.
          source.close();
          source = null;
          setSocketState('offline');
          if (!disposed && reconnectTimer === null) {
            reconnectTimer = window.setTimeout(() => {
              reconnectTimer = null;
              connect();
            }, reconnectDelayMs);
            reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
          }
        } else {
          // EventSource auto-reconnects on transient errors; onopen fires again once it succeeds.
          setSocketState('connecting');
        }
      };
      source.onmessage = (event) => {
        let envelope: LiveEnvelope;
        try {
          envelope = JSON.parse(event.data) as LiveEnvelope;
        } catch (err) {
          console.warn('Dropping malformed SSE frame', err);
          return;
        }
        if (!isChatMessageEnvelope(envelope)) {
          return;
        }

        const message = compactChatMessage(envelope.payload);
        if (!activeChannel || message.channel_login === activeChannel) {
          onMessage(message);
        }
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      source?.close();
    };
  }, [activeChannel, enabled, onMessage]);

  return socketState;
}

function isChatMessageEnvelope(envelope: LiveEnvelope): envelope is { type: 'chat_message'; payload: ChatMessage } {
  return envelope.type === 'chat_message' && typeof envelope.payload === 'object' && envelope.payload !== null;
}
