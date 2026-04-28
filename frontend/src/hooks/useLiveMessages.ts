import React from 'react';

import type { ChatMessage, LiveEnvelope, SocketState } from '../types';

function websocketUrl(): string {
  const configured = import.meta.env.VITE_WS_BASE;
  if (configured) {
    return configured;
  }
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${protocol}://${window.location.host}/ws/messages`;
}

type UseLiveMessagesArgs = {
  activeChannel: string;
  onMessage: (message: ChatMessage) => void;
};

export function useLiveMessages({ activeChannel, onMessage }: UseLiveMessagesArgs): SocketState {
  const [socketState, setSocketState] = React.useState<SocketState>('connecting');

  React.useEffect(() => {
    const socket = new WebSocket(websocketUrl());
    setSocketState('connecting');

    socket.onopen = () => setSocketState('live');
    socket.onclose = () => setSocketState('offline');
    socket.onerror = () => setSocketState('offline');
    socket.onmessage = (event) => {
      const envelope = JSON.parse(event.data) as LiveEnvelope;
      if (!isChatMessageEnvelope(envelope)) {
        return;
      }

      const message = envelope.payload;
      if (!activeChannel || message.channel_login === activeChannel) {
        onMessage(message);
      }
    };

    return () => socket.close();
  }, [activeChannel, onMessage]);

  return socketState;
}

function isChatMessageEnvelope(envelope: LiveEnvelope): envelope is { type: 'chat_message'; payload: ChatMessage } {
  return envelope.type === 'chat_message' && typeof envelope.payload === 'object' && envelope.payload !== null;
}
