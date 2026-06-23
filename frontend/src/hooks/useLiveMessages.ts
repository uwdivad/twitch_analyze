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

export function useLiveMessages({ activeChannel, enabled, onMessage }: UseLiveMessagesArgs): SocketState {
  const [socketState, setSocketState] = React.useState<SocketState>('connecting');

  React.useEffect(() => {
    if (!enabled) {
      setSocketState('offline');
      return;
    }

    setSocketState('connecting');
    const source = new EventSource(streamUrl());

    source.onopen = () => setSocketState('live');
    source.onerror = () => {
      // EventSource auto-reconnects on transient errors; onopen fires again once it succeeds.
      setSocketState(source.readyState === EventSource.CLOSED ? 'offline' : 'connecting');
    };
    source.onmessage = (event) => {
      const envelope = JSON.parse(event.data) as LiveEnvelope;
      if (!isChatMessageEnvelope(envelope)) {
        return;
      }

      const message = compactChatMessage(envelope.payload);
      if (!activeChannel || message.channel_login === activeChannel) {
        onMessage(message);
      }
    };

    return () => source.close();
  }, [activeChannel, enabled, onMessage]);

  return socketState;
}

function isChatMessageEnvelope(envelope: LiveEnvelope): envelope is { type: 'chat_message'; payload: ChatMessage } {
  return envelope.type === 'chat_message' && typeof envelope.payload === 'object' && envelope.payload !== null;
}
