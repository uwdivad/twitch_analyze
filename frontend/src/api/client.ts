import type { ChannelInfo, ChannelVolumeSeries, ChatMessage, MessageTotal, TopItem, VolumePoint } from '../types';
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
