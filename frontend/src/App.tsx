import React from 'react';

import { generateSummary, loadDashboardData, loadSummaries, loadTranscriptionJob, startTranscription } from './api/client';
import { ChannelVolumeCharts } from './components/ChannelVolumeCharts';
import { ChannelControls } from './components/ChannelControls';
import {
  DEFAULT_LIVE_UPDATE_INTERVAL_MS,
  DEFAULT_VOLUME_WINDOW_MINUTES,
  KNOWN_BOTS,
  MAX_LIVE_MESSAGES_PER_FLUSH,
  MIN_DASHBOARD_RELOAD_INTERVAL_MS,
  VOLUME_WINDOW_OPTIONS
} from './config';
import { LiveFeed } from './components/LiveFeed';
import { Metrics } from './components/Metrics';
import { SummaryPanel } from './components/SummaryPanel';
import { Topbar } from './components/Topbar';
import { TopList } from './components/TopList';
import { TranscriptionPanel } from './components/TranscriptionPanel';
import { VolumeChart } from './components/VolumeChart';
import { useLiveMessages } from './hooks/useLiveMessages';
import type {
  ChannelInfo,
  ChannelVolumeSeries,
  ChatMessage,
  ChatSummary,
  TopItem,
  TranscriptionJob,
  VolumePoint
} from './types';
import {
  buildTopChattersFromMessages,
  buildTopEmotesFromMessages,
  buildVolumeFromMessages,
  incrementTopItemsByCounts,
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
  const [summaries, setSummaries] = React.useState<ChatSummary[]>([]);
  const [volumeWindowMinutes, setVolumeWindowMinutes] = React.useState<number>(DEFAULT_VOLUME_WINDOW_MINUTES);
  const [liveUpdateIntervalMs, setLiveUpdateIntervalMs] = React.useState<number>(DEFAULT_LIVE_UPDATE_INTERVAL_MS);
  const [showLiveFeed, setShowLiveFeed] = React.useState<boolean>(true);
  const [hideBots, setHideBots] = React.useState<boolean>(false);
  const [lastUpdatedAt, setLastUpdatedAt] = React.useState<number | null>(null);
  const [isDashboardUpdating, setIsDashboardUpdating] = React.useState<boolean>(false);
  const [isSummaryGenerating, setIsSummaryGenerating] = React.useState<boolean>(false);
  const [isTranscriptionStarting, setIsTranscriptionStarting] = React.useState<boolean>(false);
  const [transcriptionJob, setTranscriptionJob] = React.useState<TranscriptionJob | null>(null);
  const [error, setError] = React.useState<string>('');
  const [summaryError, setSummaryError] = React.useState<string>('');
  const [transcriptionError, setTranscriptionError] = React.useState<string>('');
  const liveMessagesByChannel = React.useRef<Map<string, ChatMessage[]>>(new Map());
  const pendingLiveMessages = React.useRef<ChatMessage[]>([]);
  // Last server-side (ClickHouse) volume series for the current view; live
  // volume is re-derived from the live message cache and merged on top.
  const dbVolume = React.useRef<VolumePoint[]>([]);
  const dbChannelVolumes = React.useRef<ChannelVolumeSeries>({});
  // Monotonically increasing id for dashboard loads: a response is applied only
  // if no newer load has started, so a channel switch can never be clobbered by
  // a stale in-flight response.
  const dashboardRequestId = React.useRef(0);

  const activeChannel = selectedChannel;
  const volumeWindowLabel =
    VOLUME_WINDOW_OPTIONS.find((option) => option.minutes === volumeWindowMinutes)?.label ??
    `${volumeWindowMinutes}m`;

  // Rebuild the displayed volume series from the full deduped live cache merged
  // over the last ClickHouse series. Deriving from the cache (not per-flush
  // deltas) means a minute spanning several flushes counts every message rather
  // than just the largest flush.
  const recomputeVolumes = React.useCallback(() => {
    const nextChannelVolumes: ChannelVolumeSeries = { ...dbChannelVolumes.current };
    for (const [channel, channelMessages] of liveMessagesByChannel.current) {
      nextChannelVolumes[channel] = mergeVolumeSeries(
        [dbChannelVolumes.current[channel] ?? [], buildVolumeFromMessages(channelMessages, volumeWindowMinutes)],
        volumeWindowMinutes
      );
    }
    setChannelVolumes(nextChannelVolumes);

    const viewMessages = activeChannel
      ? liveMessagesByChannel.current.get(activeChannel) ?? []
      : [...liveMessagesByChannel.current.values()].flat();
    setVolume(
      mergeVolumeSeries(
        [dbVolume.current, buildVolumeFromMessages(viewMessages, volumeWindowMinutes)],
        volumeWindowMinutes
      )
    );
  }, [activeChannel, volumeWindowMinutes]);

  const applyVisibleMessages = React.useCallback((incoming: ChatMessage[]) => {
    if (incoming.length === 0) {
      return;
    }

    setMessageTotal((current) => current + incoming.length);
    setMessages((current) => mergeMessages(current, incoming));
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
  }, []);

  const handleLiveMessage = React.useCallback(
    (message: ChatMessage) => {
      const pending = pendingLiveMessages.current;
      pending.push(message);
      // Cap the queue at enqueue time (keep the most recent entries) so it
      // cannot grow unbounded between flushes during high-volume chat.
      if (pending.length > MAX_LIVE_MESSAGES_PER_FLUSH) {
        pending.shift();
      }
    },
    []
  );

  const flushPendingLiveMessages = React.useCallback(() => {
    // Already capped at MAX_LIVE_MESSAGES_PER_FLUSH at enqueue time.
    const incoming = pendingLiveMessages.current;
    if (incoming.length === 0) {
      return;
    }
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

    recomputeVolumes();
    applyVisibleMessages(
      activeChannel
        ? incoming.filter((message) => message.channel_login === activeChannel)
        : incoming
    );
  }, [activeChannel, applyVisibleMessages, recomputeVolumes]);

  React.useEffect(() => {
    if (!showLiveFeed) {
      return;
    }
    const interval = window.setInterval(flushPendingLiveMessages, liveUpdateIntervalMs);
    return () => {
      window.clearInterval(interval);
    };
  }, [flushPendingLiveMessages, liveUpdateIntervalMs, showLiveFeed]);

  const socketState = useLiveMessages({
    activeChannel: '',
    enabled: showLiveFeed,
    onMessage: handleLiveMessage
  });

  const loadDashboard = React.useCallback(async () => {
    // Capture a fresh request id; any load that started earlier becomes stale
    // and its response is discarded below instead of clobbering newer state.
    const requestId = ++dashboardRequestId.current;
    setIsDashboardUpdating(true);
    try {
      setError('');
      const dashboard = await loadDashboardData(activeChannel, volumeWindowMinutes);
      const nextSummaries = activeChannel ? await loadSummaries(activeChannel) : [];
      if (requestId !== dashboardRequestId.current) {
        return;
      }

      // Clean up in-memory live messages that are already durable in ClickHouse:
      // evict a message if its message_id appears in the DB response, or if it is
      // strictly older than the latest DB timestamp. Equal-timestamp messages are
      // kept unless their id was seen — they may not be inserted yet.
      const dbMessageIds = new Set(dashboard.messages.map((m) => m.message_id));
      const latestDbTimestamp = dashboard.messages.length > 0
        ? Math.max(...dashboard.messages.map((m) => new Date(m.event_ts).getTime()))
        : 0;

      for (const [channel, channelMsgs] of liveMessagesByChannel.current) {
        liveMessagesByChannel.current.set(
          channel,
          channelMsgs.filter(
            (m) => !dbMessageIds.has(m.message_id) && new Date(m.event_ts).getTime() >= latestDbTimestamp
          )
        );
      }

      const liveMessages = activeChannel
        ? liveMessagesByChannel.current.get(activeChannel) ?? []
        : Array.from(liveMessagesByChannel.current.values()).flat();

      const mergedMessages = mergeMessages(dashboard.messages, liveMessages);
      const liveVolume = buildVolumeFromMessages(liveMessages, volumeWindowMinutes);

      dbVolume.current = dashboard.volume;
      dbChannelVolumes.current = dashboard.channelVolumes;

      setChannels(dashboard.channels);
      setMessages(mergedMessages);
      setMessageTotal(dashboard.messageTotal + liveMessages.length);

      const nextChannelVolumes: ChannelVolumeSeries = { ...dashboard.channelVolumes };
      for (const [channel, channelMessages] of liveMessagesByChannel.current) {
        nextChannelVolumes[channel] = mergeVolumeSeries(
          [dashboard.channelVolumes[channel] ?? [], buildVolumeFromMessages(channelMessages, volumeWindowMinutes)],
          volumeWindowMinutes
        );
      }
      setChannelVolumes(nextChannelVolumes);

      // Merge the ClickHouse series with the live in-memory series; overlapping
      // boundary minutes take the per-bucket max (see mergeVolumeSeries for why
      // max, not sum).
      setVolume(mergeVolumeSeries([dashboard.volume, liveVolume], volumeWindowMinutes));

      // Combine ClickHouse top lists with counts from the active live messages in memory
      const chatterCounts = new Map<string, number>();
      const emoteCounts = new Map<string, number>();
      for (const message of liveMessages) {
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

      setTopChatters(incrementTopItemsByCounts(
        dashboard.topChatters.length > 0 ? dashboard.topChatters : buildTopChattersFromMessages(dashboard.messages),
        chatterCounts
      ));
      setTopEmotes(incrementTopItemsByCounts(
        dashboard.topEmotes.length > 0 ? dashboard.topEmotes : buildTopEmotesFromMessages(dashboard.messages),
        emoteCounts
      ));

      setSummaries(nextSummaries);
      setLastUpdatedAt(Date.now());
    } catch (err) {
      if (requestId === dashboardRequestId.current) {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard');
      }
    } finally {
      // Always clear the in-flight indicator (unless a newer load owns it now),
      // so one hung/failed request can never freeze future reloads.
      if (requestId === dashboardRequestId.current) {
        setIsDashboardUpdating(false);
      }
    }
  }, [activeChannel, volumeWindowMinutes]);

  const handleGenerateSummary = React.useCallback(async (windowMinutes: number) => {
    if (!activeChannel || isSummaryGenerating) {
      return;
    }

    setIsSummaryGenerating(true);
    try {
      setSummaryError('');
      const summary = await generateSummary(activeChannel, windowMinutes);
      setSummaries((current) => [summary, ...current.filter((item) => item.summary_id !== summary.summary_id)]);
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to generate summary');
    } finally {
      setIsSummaryGenerating(false);
    }
  }, [activeChannel, isSummaryGenerating]);

  const handleStartTranscription = React.useCallback(async (channel: string, durationMinutes: number) => {
    if (isTranscriptionStarting) {
      return;
    }

    setIsTranscriptionStarting(true);
    try {
      setTranscriptionError('');
      const job = await startTranscription(channel, durationMinutes);
      setTranscriptionJob(job);
    } catch (err) {
      setTranscriptionError(err instanceof Error ? err.message : 'Failed to start transcription');
    } finally {
      setIsTranscriptionStarting(false);
    }
  }, [isTranscriptionStarting]);

  React.useEffect(() => {
    if (!transcriptionJob || transcriptionJob.status !== 'running') {
      return;
    }

    const interval = window.setInterval(async () => {
      try {
        const job = await loadTranscriptionJob(transcriptionJob.job_id);
        setTranscriptionJob(job);
      } catch (err) {
        setTranscriptionError(err instanceof Error ? err.message : 'Failed to refresh transcription status');
      }
    }, 5000);
    return () => window.clearInterval(interval);
  }, [transcriptionJob]);

  const dashboardReloadIntervalMs = Math.max(liveUpdateIntervalMs, MIN_DASHBOARD_RELOAD_INTERVAL_MS);

  React.useEffect(() => {
    loadDashboard();
    const interval = window.setInterval(loadDashboard, dashboardReloadIntervalMs);
    return () => window.clearInterval(interval);
  }, [loadDashboard, dashboardReloadIntervalMs]);

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
  const visibleTopChatters = React.useMemo(
    () =>
      hideBots ? topChatters.filter((item) => !KNOWN_BOTS.has(item.value.toLowerCase())) : topChatters,
    [topChatters, hideBots]
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
        lastUpdatedAt={lastUpdatedAt}
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
        channelLabel={activeChannel ? `#${activeChannel}` : 'all channels'}
        windowLabel={volumeWindowLabel}
      />

      <div className={`dashboard-layout${showLiveFeed ? ' with-feed' : ''}`}>
        <div className="dashboard-main">
          <section className="grid">
            <VolumeChart volume={volume} windowLabel={volumeWindowLabel} windowMinutes={volumeWindowMinutes} />
            <TopList title="Top Chatters" items={visibleTopChatters} />
            <TopList title="Top Emotes" items={topEmotes} />
          </section>

          {!activeChannel ? (
            <ChannelVolumeCharts
              channels={channels}
              channelVolumes={channelVolumes}
              windowLabel={volumeWindowLabel}
            />
          ) : null}
        </div>

        {showLiveFeed ? (
          <aside className="dashboard-side">
            <LiveFeed
              activeChannel={activeChannel}
              messages={messages}
              hideBots={hideBots}
              onHideBotsChange={setHideBots}
            />
          </aside>
        ) : null}
      </div>

      <SummaryPanel
        activeChannel={activeChannel}
        summaries={summaries}
        isLoading={isSummaryGenerating}
        error={summaryError}
        onGenerate={handleGenerateSummary}
      />

      <TranscriptionPanel
        defaultChannel={activeChannel}
        job={transcriptionJob}
        isStarting={isTranscriptionStarting}
        error={transcriptionError}
        onStart={handleStartTranscription}
      />
    </main>
  );
}
