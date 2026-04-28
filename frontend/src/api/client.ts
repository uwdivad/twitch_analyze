import type { ChannelInfo, ChatMessage, TopItem, VolumePoint } from '../types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function channelQuery(channel: string): string {
  return channel ? `?channel=${encodeURIComponent(channel)}` : '';
}

function appendLimit(query: string, limit: number): string {
  return `${query}${query ? '&' : '?'}limit=${limit}`;
}

export async function loadDashboardData(channel: string) {
  const query = channelQuery(channel);
  const [channels, messages, volume, topChatters, topEmotes] = await Promise.all([
    getJson<ChannelInfo[]>('/api/channels'),
    getJson<ChatMessage[]>(`/api/messages/recent${appendLimit(query, 150)}`),
    getJson<VolumePoint[]>(`/api/analytics/volume${appendLimit(query, 120)}`),
    getJson<TopItem[]>(`/api/analytics/top-chatters${appendLimit(query, 10)}`),
    getJson<TopItem[]>(`/api/analytics/top-emotes${appendLimit(query, 10)}`)
  ]);

  return {
    channels,
    messages,
    volume,
    topChatters,
    topEmotes
  };
}
