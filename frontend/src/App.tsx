import React from 'react';

import { loadDashboardData } from './api/client';
import { ChannelVolumeCharts } from './components/ChannelVolumeCharts';
import { ChannelControls } from './components/ChannelControls';
import {
  DEFAULT_LIVE_UPDATE_INTERVAL_MS,
  DEFAULT_VOLUME_WINDOW_MINUTES,
  MAX_LIVE_MESSAGES_PER_FLUSH,
  VOLUME_WINDOW_OPTIONS
} from './config';
import { LiveFeed } from './components/LiveFeed';
import { Metrics } from './components/Metrics';
import { Topbar } from './components/Topbar';
import { TopList } from './components/TopList';
import { VolumeChart } from './components/VolumeChart';
import { useLiveMessages } from './hooks/useLiveMessages';
import type { ChannelInfo, ChannelVolumeSeries, ChatMessage, TopItem, VolumePoint } from './types';
import {
  buildTopChattersFromMessages,
  buildTopEmotesFromMessages,
  buildVolumeFromMessages,
  incrementTopItemsByCounts,
  incrementVolume,
  mergeMessages,
  mergeVolumeSeries
} from './utils/analytics';

export function App() {
  const [channels, setChannels] = React.useState<ChannelInfo[]>([]);
  const [selectedChannel, setSelectedChannel] = React.useState<string>('');
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [messageTotal, setMessageTotal] = React.useState<number>(0);
  const [volume, setVolume] = React.useState<VolumePoint[]>([]);
  const [channelVolumes, setChannelVolumes] = React.useState<ChannelVolumeSeries>({});
  const [topChatters, setTopChatters] = React.useState<TopItem[]>([]);
  const [topEmotes, setTopEmotes] = React.useState<TopItem[]>([]);
  const [volumeWindowMinutes, setVolumeWindowMinutes] = React.useState<number>(DEFAULT_VOLUME_WINDOW_MINUTES);
  const [liveUpdateIntervalMs, setLiveUpdateIntervalMs] = React.useState<number>(DEFAULT_LIVE_UPDATE_INTERVAL_MS);
  const [showLiveFeed, setShowLiveFeed] = React.useState<boolean>(true);
  const [isDashboardUpdating, setIsDashboardUpdating] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string>('');
  const minuteChatters = React.useRef<Map<string, Set<string>>>(new Map());
  const liveMessagesByChannel = React.useRef<Map<string, ChatMessage[]>>(new Map());
  const pendingLiveMessages = React.useRef<ChatMessage[]>([]);
  const displayedVolumeChannel = React.useRef<string>('');
  const isDashboardUpdatingRef = React.useRef(false);

  const activeChannel = selectedChannel;
  const volumeWindowLabel =
    VOLUME_WINDOW_OPTIONS.find((option) => option.minutes === volumeWindowMinutes)?.label ??
    `${volumeWindowMinutes}m`;

  const applyVisibleMessages = React.useCallback((incoming: ChatMessage[]) => {
    if (incoming.length === 0) {
      return;
    }

    setMessageTotal((current) => current + incoming.length);
    setMessages((current) => mergeMessages(current, incoming));
    setChannelVolumes((current) => {
      const next = { ...current };
      const byChannel = new Map<string, ChatMessage[]>();
      for (const message of incoming) {
        const channelMessages = byChannel.get(message.channel_login) ?? [];
        channelMessages.push(message);
        byChannel.set(message.channel_login, channelMessages);
      }

      for (const [channel, channelMessages] of byChannel) {
        next[channel] = mergeVolumeSeries(
          [next[channel] ?? [], buildVolumeFromMessages(channelMessages, volumeWindowMinutes)],
          volumeWindowMinutes
        );
      }
      return next;
    });
    setVolume((current) =>
      incoming.reduce(
        (next, message) => incrementVolume(next, message, minuteChatters.current, volumeWindowMinutes),
        current
      )
    );
    const chatterCounts = new Map<string, number>();
    const emoteCounts = new Map<string, number>();
    for (const message of incoming) {
      const chatter = message.chatter_login || message.chatter_display_name;
      if (chatter) {
        chatterCounts.set(chatter, (chatterCounts.get(chatter) ?? 0) + 1);
      }
      for (const emote of message.emotes) {
        const label = String(emote.text ?? '');
        if (label) {
          emoteCounts.set(label, (emoteCounts.get(label) ?? 0) + 1);
        }
      }
    }
    setTopChatters((current) => incrementTopItemsByCounts(current, chatterCounts));
    setTopEmotes((current) => incrementTopItemsByCounts(current, emoteCounts));
  }, [volumeWindowMinutes]);

  const handleLiveMessage = React.useCallback(
    (message: ChatMessage) => {
      pendingLiveMessages.current.push(message);
    },
    []
  );

  const flushPendingLiveMessages = React.useCallback(() => {
    const pending = pendingLiveMessages.current;
    if (pending.length === 0) {
      return;
    }
    const incoming =
      pending.length > MAX_LIVE_MESSAGES_PER_FLUSH
        ? pending.slice(-MAX_LIVE_MESSAGES_PER_FLUSH)
        : pending;
    pendingLiveMessages.current = [];

    const byChannel = new Map<string, ChatMessage[]>();
    for (const message of incoming) {
      const channelMessages = byChannel.get(message.channel_login) ?? [];
      channelMessages.push(message);
      byChannel.set(message.channel_login, channelMessages);
    }

    for (const [channel, channelIncoming] of byChannel) {
      const channelMessages = liveMessagesByChannel.current.get(channel) ?? [];
      liveMessagesByChannel.current.set(channel, mergeMessages(channelMessages, channelIncoming));
    }

    applyVisibleMessages(
      activeChannel
        ? incoming.filter((message) => message.channel_login === activeChannel)
        : incoming
    );
  }, [activeChannel, applyVisibleMessages]);

  React.useEffect(() => {
    const interval = window.setInterval(flushPendingLiveMessages, liveUpdateIntervalMs);
    return () => {
      window.clearInterval(interval);
    };
  }, [flushPendingLiveMessages, liveUpdateIntervalMs]);

  const socketState = useLiveMessages({
    activeChannel: '',
    onMessage: handleLiveMessage
  });

  const loadDashboard = React.useCallback(async () => {
    if (isDashboardUpdatingRef.current) {
      return;
    }
    isDashboardUpdatingRef.current = true;
    setIsDashboardUpdating(true);
    try {
      setError('');
      const dashboard = await loadDashboardData(activeChannel, volumeWindowMinutes);
      const liveMessages = activeChannel
        ? liveMessagesByChannel.current.get(activeChannel) ?? []
        : Array.from(liveMessagesByChannel.current.values()).flat();
      const mergedMessages = mergeMessages(dashboard.messages, liveMessages);
      const liveVolume = buildVolumeFromMessages(mergedMessages, volumeWindowMinutes);
      const volumeViewKey = `${activeChannel || 'all'}:${volumeWindowMinutes}`;
      const isSameDisplayedChannel = displayedVolumeChannel.current === volumeViewKey;

      setChannels(dashboard.channels);
      setMessages(mergedMessages);
      setMessageTotal(dashboard.messageTotal);
      setChannelVolumes(dashboard.channelVolumes);
      setVolume((current) =>
        isSameDisplayedChannel
          ? mergeVolumeSeries([current, dashboard.volume, liveVolume], volumeWindowMinutes)
          : mergeVolumeSeries([dashboard.volume, liveVolume], volumeWindowMinutes)
      );
      setTopChatters(dashboard.topChatters.length > 0 ? dashboard.topChatters : buildTopChattersFromMessages(mergedMessages));
      setTopEmotes(dashboard.topEmotes.length > 0 ? dashboard.topEmotes : buildTopEmotesFromMessages(mergedMessages));
      minuteChatters.current.clear();
      displayedVolumeChannel.current = volumeViewKey;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      isDashboardUpdatingRef.current = false;
      setIsDashboardUpdating(false);
    }
  }, [activeChannel, volumeWindowMinutes]);

  React.useEffect(() => {
    loadDashboard();
    const interval = window.setInterval(loadDashboard, liveUpdateIntervalMs);
    return () => window.clearInterval(interval);
  }, [loadDashboard, liveUpdateIntervalMs]);

  const uniqueChatters = React.useMemo(
    () => new Set(messages.map((message) => message.chatter_login)).size,
    [messages]
  );
  const latestRate = React.useMemo(
    () => (volume.length > 0 ? volume[volume.length - 1].message_count : 0),
    [volume]
  );
  const peakMinute = React.useMemo(
    () => volume.reduce((max, point) => Math.max(max, point.message_count), 0),
    [volume]
  );

  return (
    <main className="shell">
      <Topbar socketState={socketState} />
      <ChannelControls
        channels={channels}
        selectedChannel={selectedChannel}
        volumeWindowMinutes={volumeWindowMinutes}
        liveUpdateIntervalMs={liveUpdateIntervalMs}
        showLiveFeed={showLiveFeed}
        isUpdating={isDashboardUpdating}
        onChannelChange={setSelectedChannel}
        onVolumeWindowChange={setVolumeWindowMinutes}
        onLiveUpdateIntervalChange={setLiveUpdateIntervalMs}
        onShowLiveFeedChange={setShowLiveFeed}
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
        <VolumeChart volume={volume} windowLabel={volumeWindowLabel} />
        <TopList title="Top Chatters" items={topChatters} />
        <TopList title="Top Emotes" items={topEmotes} />
      </section>

      {!activeChannel ? (
        <ChannelVolumeCharts
          channels={channels}
          channelVolumes={channelVolumes}
          windowLabel={volumeWindowLabel}
        />
      ) : null}

      {showLiveFeed ? <LiveFeed activeChannel={activeChannel} messages={messages} /> : null}
    </main>
  );
}
