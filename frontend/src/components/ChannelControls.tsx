import { RefreshCw } from 'lucide-react';

import type { ChannelInfo } from '../types';

type ChannelControlsProps = {
  channels: ChannelInfo[];
  selectedChannel: string;
  onChannelChange: (channel: string) => void;
  onRefresh: () => void;
};

export function ChannelControls({
  channels,
  selectedChannel,
  onChannelChange,
  onRefresh
}: ChannelControlsProps) {
  return (
    <section className="controls">
      <label>
        <span>Channel</span>
        <select value={selectedChannel} onChange={(event) => onChannelChange(event.target.value)}>
          {channels.length === 0 ? <option value="">No configured channels</option> : null}
          {channels.map((channel) => (
            <option key={channel.channel_login} value={channel.channel_login}>
              {channel.channel_display_name || channel.channel_login}
            </option>
          ))}
        </select>
      </label>
      <button onClick={onRefresh} type="button">
        <RefreshCw size={16} />
        Refresh
      </button>
    </section>
  );
}
