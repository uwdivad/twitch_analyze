import React from 'react';

import { loadDashboardData } from './api/client';
import { ChannelControls } from './components/ChannelControls';
import { LiveFeed } from './components/LiveFeed';
import { Metrics } from './components/Metrics';
import { Topbar } from './components/Topbar';
import { TopList } from './components/TopList';
import { VolumeChart } from './components/VolumeChart';
import { useLiveMessages } from './hooks/useLiveMessages';
import type { ChannelInfo, ChatMessage, TopItem, VolumePoint } from './types';
import {
  buildTopChattersFromMessages,
  buildTopEmotesFromMessages,
  buildVolumeFromMessages,
  incrementTopItems,
  incrementVolume,
  mergeMessages,
  mergeVolumeSeries
} from './utils/analytics';

export function App() {
  const [channels, setChannels] = React.useState<ChannelInfo[]>([]);
  const [selectedChannel, setSelectedChannel] = React.useState<string>('');
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [volume, setVolume] = React.useState<VolumePoint[]>([]);
  const [topChatters, setTopChatters] = React.useState<TopItem[]>([]);
  const [topEmotes, setTopEmotes] = React.useState<TopItem[]>([]);
  const [error, setError] = React.useState<string>('');
  const minuteChatters = React.useRef<Map<string, Set<string>>>(new Map());
  const liveMessagesByChannel = React.useRef<Map<string, ChatMessage[]>>(new Map());
  const displayedVolumeChannel = React.useRef<string>('');

  const activeChannel = selectedChannel || channels[0]?.channel_login || '';

  const applyVisibleMessage = React.useCallback((message: ChatMessage) => {
    setMessages((current) => [...current.slice(-149), message]);
    setVolume((current) => incrementVolume(current, message, minuteChatters.current));
    setTopChatters((current) => incrementTopItems(current, message.chatter_login || message.chatter_display_name));

    for (const emote of message.emotes) {
      const label = String(emote.text ?? '');
      if (label) {
        setTopEmotes((current) => incrementTopItems(current, label));
      }
    }
  }, []);

  const handleLiveMessage = React.useCallback(
    (message: ChatMessage) => {
      const channelMessages = liveMessagesByChannel.current.get(message.channel_login) ?? [];
      liveMessagesByChannel.current.set(message.channel_login, mergeMessages(channelMessages, [message]));

      if (!activeChannel || message.channel_login === activeChannel) {
        applyVisibleMessage(message);
      }
    },
    [activeChannel, applyVisibleMessage]
  );

  const socketState = useLiveMessages({
    activeChannel: '',
    onMessage: handleLiveMessage
  });

  const loadDashboard = React.useCallback(async () => {
    try {
      setError('');
      const dashboard = await loadDashboardData(activeChannel);
      const liveMessages = activeChannel ? liveMessagesByChannel.current.get(activeChannel) ?? [] : [];
      const mergedMessages = mergeMessages(dashboard.messages, liveMessages);
      const liveVolume = buildVolumeFromMessages(mergedMessages);
      const isSameDisplayedChannel = displayedVolumeChannel.current === activeChannel;

      setChannels(dashboard.channels);
      setMessages(mergedMessages);
      setVolume((current) =>
        isSameDisplayedChannel
          ? mergeVolumeSeries(current, dashboard.volume, liveVolume)
          : mergeVolumeSeries(dashboard.volume, liveVolume)
      );
      setTopChatters(dashboard.topChatters.length > 0 ? dashboard.topChatters : buildTopChattersFromMessages(mergedMessages));
      setTopEmotes(dashboard.topEmotes.length > 0 ? dashboard.topEmotes : buildTopEmotesFromMessages(mergedMessages));
      minuteChatters.current.clear();
      displayedVolumeChannel.current = activeChannel;

      if (!selectedChannel && dashboard.channels.length > 0) {
        setSelectedChannel(dashboard.channels[0].channel_login);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    }
  }, [activeChannel, selectedChannel]);

  React.useEffect(() => {
    loadDashboard();
    const interval = window.setInterval(loadDashboard, 30_000);
    return () => window.clearInterval(interval);
  }, [loadDashboard]);

  const messageTotal = messages.length;
  const uniqueChatters = new Set(messages.map((message) => message.chatter_login)).size;
  const latestRate = volume.length > 0 ? volume[volume.length - 1].message_count : 0;
  const peakMinute = volume.reduce((max, point) => Math.max(max, point.message_count), 0);

  return (
    <main className="shell">
      <Topbar socketState={socketState} />
      <ChannelControls
        channels={channels}
        selectedChannel={selectedChannel}
        onChannelChange={setSelectedChannel}
        onRefresh={loadDashboard}
      />

      {error ? <div className="error">{error}</div> : null}

      <Metrics
        messageTotal={messageTotal}
        uniqueChatters={uniqueChatters}
        latestRate={latestRate}
        peakMinute={peakMinute}
      />

      <section className="grid">
        <VolumeChart volume={volume} />
        <TopList title="Top Chatters" items={topChatters} />
        <TopList title="Top Emotes" items={topEmotes} />
      </section>

      <LiveFeed activeChannel={activeChannel} messages={messages} />
    </main>
  );
}
