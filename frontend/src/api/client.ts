import type {
  ChannelInfo,
  ChannelVolumeSeries,
  ChatMessage,
  ChatSummary,
  MessageTotal,
  TopItem,
  TranscriptionJob,
  VolumePoint
} from '../types';
import { RECENT_MESSAGE_LIMIT } from '../config';
import { compactChatMessages } from '../utils/messages';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === 'string' ? payload.detail : detail;
    } catch {
      // Keep the HTTP status fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.json();
}

function channelQuery(channel: string): string {
  return channel ? `?channel=${encodeURIComponent(channel)}` : '';
}

function appendLimit(query: string, limit: number): string {
  return `${query}${query ? '&' : '?'}limit=${limit}`;
}

export async function loadDashboardData(channel: string, volumeWindowMinutes: number) {
  const channels = await getJson<ChannelInfo[]>('/api/channels');
  const query = channelQuery(channel);
  const [messages, volume, messageTotal, topChatters, topEmotes] = await Promise.all([
    getJson<ChatMessage[]>(`/api/messages/recent${appendLimit(query, RECENT_MESSAGE_LIMIT)}`),
    getJson<VolumePoint[]>(`/api/analytics/volume${appendLimit(query, volumeWindowMinutes)}`),
    channel
      ? getJson<MessageTotal>(`/api/analytics/message-total${query}`)
      : Promise.all(
          channels.map((channelInfo) =>
            getJson<MessageTotal>(`/api/analytics/message-total${channelQuery(channelInfo.channel_login)}`)
          )
        ).then((totals) => ({
          count: totals.reduce((sum, total) => sum + total.count, 0)
        })),
    getJson<TopItem[]>(`/api/analytics/top-chatters${appendLimit(query, 10)}`),
    getJson<TopItem[]>(`/api/analytics/top-emotes${appendLimit(query, 10)}`)
  ]);
  const channelVolumes: ChannelVolumeSeries = channel
    ? {}
    : Object.fromEntries(
        await Promise.all(
          channels.map(async (channelInfo) => [
            channelInfo.channel_login,
            await getJson<VolumePoint[]>(
              `/api/analytics/volume${appendLimit(channelQuery(channelInfo.channel_login), volumeWindowMinutes)}`
            )
          ])
        )
      );

  return {
    channels,
    messages: compactChatMessages(messages),
    messageTotal: messageTotal.count,
    volume,
    channelVolumes,
    topChatters,
    topEmotes
  };
}

export async function loadSummaries(channel: string, limit = 10): Promise<ChatSummary[]> {
  const query = appendLimit(channelQuery(channel), limit);
  return getJson<ChatSummary[]>(`/api/summaries${query}`);
}

export async function generateSummary(channel: string, windowMinutes: number): Promise<ChatSummary> {
  return postJson<ChatSummary>('/api/summaries/generate', {
    channel,
    window_minutes: windowMinutes
  });
}

export async function startTranscription(channel: string, durationMinutes: number): Promise<TranscriptionJob> {
  return postJson<TranscriptionJob>('/api/transcriptions/start', {
    channel,
    duration_minutes: durationMinutes
  });
}

export async function loadTranscriptionJob(jobId: string): Promise<TranscriptionJob> {
  return getJson<TranscriptionJob>(`/api/transcriptions/jobs/${encodeURIComponent(jobId)}`);
}
