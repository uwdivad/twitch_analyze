import React from 'react';
import { RefreshCw } from 'lucide-react';

import { LIVE_UPDATE_INTERVAL_OPTIONS, VOLUME_WINDOW_OPTIONS } from '../config';
import type { ChannelInfo } from '../types';
import { formatSecondsAgo } from '../utils/format';

type ChannelControlsProps = {
  channels: ChannelInfo[];
  selectedChannel: string;
  volumeWindowMinutes: number;
  liveUpdateIntervalMs: number;
  showLiveFeed: boolean;
  isUpdating: boolean;
  lastUpdatedAt: number | null;
  onChannelChange: (channel: string) => void;
  onVolumeWindowChange: (minutes: number) => void;
  onLiveUpdateIntervalChange: (milliseconds: number) => void;
  onShowLiveFeedChange: (show: boolean) => void;
  onRefresh: () => void;
};

function LastUpdated({ lastUpdatedAt }: { lastUpdatedAt: number | null }) {
  const [, forceTick] = React.useReducer((tick: number) => tick + 1, 0);

  React.useEffect(() => {
    const interval = window.setInterval(forceTick, 5000);
    return () => window.clearInterval(interval);
  }, []);

  if (!lastUpdatedAt) {
    return null;
  }
  return <span className="controls-updated">Updated {formatSecondsAgo(Date.now() - lastUpdatedAt)}</span>;
}

export function ChannelControls({
  channels,
  selectedChannel,
  volumeWindowMinutes,
  liveUpdateIntervalMs,
  showLiveFeed,
  isUpdating,
  lastUpdatedAt,
  onChannelChange,
  onVolumeWindowChange,
  onLiveUpdateIntervalChange,
  onShowLiveFeedChange,
  onRefresh
}: ChannelControlsProps) {
  return (
    <section className="controls" aria-busy={isUpdating}>
      <label>
        <span>Channel</span>
        <select
          value={selectedChannel}
          onChange={(event) => onChannelChange(event.target.value)}
        >
          <option value="">All channels</option>
          {channels.length === 0 ? <option disabled>No configured channels</option> : null}
          {channels.map((channel) => (
            <option key={channel.channel_login} value={channel.channel_login}>
              {channel.channel_display_name || channel.channel_login}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Time window</span>
        <select
          value={volumeWindowMinutes}
          onChange={(event) => onVolumeWindowChange(Number(event.target.value))}
        >
          {VOLUME_WINDOW_OPTIONS.map((option) => (
            <option key={option.minutes} value={option.minutes}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Update every</span>
        <select
          value={liveUpdateIntervalMs}
          onChange={(event) => onLiveUpdateIntervalChange(Number(event.target.value))}
        >
          {LIVE_UPDATE_INTERVAL_OPTIONS.map((option) => (
            <option key={option.milliseconds} value={option.milliseconds}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <div className="toggle-control" title="Streams messages over SSE; off pauses live updates and hides the feed">
        <span>Live updates</span>
        <div className="switch-wrapper">
          <label className="switch">
            <input
              aria-label="Live updates"
              checked={showLiveFeed}
              onChange={(event) => onShowLiveFeedChange(event.target.checked)}
              type="checkbox"
            />
            <span className="slider"></span>
          </label>
        </div>
      </div>
      <div className="controls-status">
        <LastUpdated lastUpdatedAt={lastUpdatedAt} />
        <button className="button-ghost" disabled={isUpdating} onClick={onRefresh} type="button">
          <RefreshCw size={16} />
          {isUpdating ? 'Updating' : 'Refresh'}
        </button>
      </div>
    </section>
  );
}
