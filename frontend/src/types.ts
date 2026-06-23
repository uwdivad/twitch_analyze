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

export type MessageTotal = {
  count: number;
};

export type ChannelVolumeSeries = Record<string, VolumePoint[]>;

export type TopItem = {
  value: string;
  count: number;
};

export type SummarySourceStats = {
  message_count: number;
  unique_chatter_count: number;
  top_chatters: TopItem[];
  top_emotes: TopItem[];
  spike_windows: Array<{
    bucket: string;
    message_count: number;
    unique_chatter_count: number;
  }>;
  sampled_message_count: number;
};

export type ChatSummary = {
  summary_id: string;
  channel_login: string;
  session_id: string;
  window_start: string;
  window_end: string;
  window_size: string;
  summary_text: string;
  model: string;
  source_stats: SummarySourceStats;
  created_at: string;
};

export type TranscriptionJob = {
  job_id: string;
  channel_login: string;
  duration_seconds: number;
  status: string;
  started_at: string;
  ends_at: string;
  detail: string;
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
