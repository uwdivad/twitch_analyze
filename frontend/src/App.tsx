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
import { incrementTopItems, incrementVolume } from './utils/analytics';

export function App() {
  const [channels, setChannels] = React.useState<ChannelInfo[]>([]);
  const [selectedChannel, setSelectedChannel] = React.useState<string>('');
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [volume, setVolume] = React.useState<VolumePoint[]>([]);
  const [topChatters, setTopChatters] = React.useState<TopItem[]>([]);
  const [topEmotes, setTopEmotes] = React.useState<TopItem[]>([]);
  const [error, setError] = React.useState<string>('');
  const minuteChatters = React.useRef<Map<string, Set<string>>>(new Map());

  const activeChannel = selectedChannel || channels[0]?.channel_login || '';

  const applyLiveMessage = React.useCallback((message: ChatMessage) => {
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

  const socketState = useLiveMessages({
    activeChannel,
    onMessage: applyLiveMessage
  });

  const loadDashboard = React.useCallback(async () => {
    try {
      setError('');
      const dashboard = await loadDashboardData(activeChannel);
      setChannels(dashboard.channels);
      setMessages(dashboard.messages);
      setVolume(dashboard.volume);
      setTopChatters(dashboard.topChatters);
      setTopEmotes(dashboard.topEmotes);
      minuteChatters.current.clear();

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
