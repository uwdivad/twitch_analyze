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

export type AppView = 'live' | 'vods';

export type VodJobStatus = 'queued' | 'fetching' | 'ingesting' | 'analyzing' | 'completed' | 'failed';

export type VodAnalysisJob = {
  video_id: string;
  status: VodJobStatus;
  started_at: string;
  updated_at: string;
  pages_fetched: number;
  fetched_comments: number;
  stored_comments: number;
  duration_seconds: number;
  last_offset_seconds: number;
  detail: string;
  error: string;
};

export type VodPeak = {
  peak_id: number;
  start_seconds: number;
  end_seconds: number;
  peak_seconds: number;
  message_count: number;
  peak_bucket_count: number;
  messages_per_second: number;
  score: number;
  baseline: number;
  top_emotes: TopItem[];
  top_tokens: TopItem[];
  sample_messages: string[];
  label: string;
  title: string;
};

export type VodAnalysis = {
  video_id: string;
  channel_id: string;
  channel_login: string;
  channel_display_name: string;
  title: string;
  video_created_at: string;
  duration_seconds: number;
  bucket_seconds: number;
  message_count: number;
  unique_chatter_count: number;
  status: string;
  error: string;
  peaks: VodPeak[];
  label_model: string;
  analyzed_at: string;
  updated_at: string;
};

export type VodAnalysisResponse = {
  job: VodAnalysisJob | null;
  analysis: VodAnalysis | null;
};

export type VodActivityBucket = {
  index: number;
  offset_seconds: number;
  message_count: number;
  unique_chatter_count: number;
};

export type VodActivity = {
  video_id: string;
  bucket_seconds: number;
  duration_seconds: number;
  buckets: VodActivityBucket[];
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
