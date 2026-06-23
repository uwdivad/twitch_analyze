import { RefreshCw } from 'lucide-react';

import { LIVE_UPDATE_INTERVAL_OPTIONS, VOLUME_WINDOW_OPTIONS } from '../config';
import type { ChannelInfo } from '../types';

type ChannelControlsProps = {
  channels: ChannelInfo[];
  selectedChannel: string;
  volumeWindowMinutes: number;
  liveUpdateIntervalMs: number;
  showLiveFeed: boolean;
  isUpdating: boolean;
  onChannelChange: (channel: string) => void;
  onVolumeWindowChange: (minutes: number) => void;
  onLiveUpdateIntervalChange: (milliseconds: number) => void;
  onShowLiveFeedChange: (show: boolean) => void;
  onRefresh: () => void;
};

export function ChannelControls({
  channels,
  selectedChannel,
  volumeWindowMinutes,
  liveUpdateIntervalMs,
  showLiveFeed,
  isUpdating,
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
        <span>Chart window</span>
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
        <span>Display data</span>
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
      <div className="toggle-control">
        <span>Live feed</span>
        <div className="switch-wrapper">
          <label className="switch">
            <input
              checked={showLiveFeed}
              onChange={(event) => onShowLiveFeedChange(event.target.checked)}
              type="checkbox"
            />
            <span className="slider"></span>
          </label>
        </div>
      </div>
      <button disabled={isUpdating} onClick={onRefresh} type="button">
        <RefreshCw size={16} />
        {isUpdating ? 'Updating' : 'Refresh'}
      </button>
    </section>
  );
}
