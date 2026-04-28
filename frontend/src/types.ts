export type ChannelInfo = {
  channel_id: string;
  channel_login: string;
  channel_display_name: string;
  status: string;
  detail?: string | null;
};

export type ChatMessage = {
  message_id: string;
  channel_login: string;
  channel_display_name: string;
  session_id: string;
  chatter_login: string;
  chatter_display_name: string;
  message_text: string;
  badges: Array<Record<string, unknown>>;
  emotes: Array<Record<string, unknown>>;
  event_ts: string;
  received_at: string;
};

export type VolumePoint = {
  bucket: string;
  message_count: number;
  unique_chatter_count: number;
};

export type TopItem = {
  value: string;
  count: number;
};

export type SocketState = 'connecting' | 'live' | 'offline';

export type LiveEnvelope =
  | {
      type: 'chat_message';
      payload: ChatMessage;
    }
  | {
      type: string;
      payload: unknown;
    };
