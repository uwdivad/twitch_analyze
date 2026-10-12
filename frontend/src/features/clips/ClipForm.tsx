import React from 'react';

import { Segmented } from '../../components/ui/Segmented';
import type { ClipPlatform } from '../../types';

export const DURATION_OPTIONS: ReadonlyArray<{ value: '5' | '10' | '15'; label: string }> = [
  { value: '5', label: '5 min' },
  { value: '10', label: '10 min' },
  { value: '15', label: '15 min' }
];

type DurationValue = (typeof DURATION_OPTIONS)[number]['value'];

const PLATFORM_OPTIONS: ReadonlyArray<{ value: ClipPlatform; label: string }> = [{ value: 'twitch', label: 'Twitch' }];

type ClipFormProps = {
  initialChannel: string;
  isStarting: boolean;
  onCreate: (channel: string, durationMinutes: number, platform: ClipPlatform) => void;
};

export function ClipForm({ initialChannel, isStarting, onCreate }: ClipFormProps) {
  const [channel, setChannel] = React.useState(initialChannel);
  const [platform, setPlatform] = React.useState<ClipPlatform>('twitch');
  const [duration, setDuration] = React.useState<DurationValue>('5');
  const channelId = React.useId();
  const platformId = React.useId();
  const canSubmit = channel.trim().length > 0 && !isStarting;

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (canSubmit) {
      onCreate(channel.trim(), Number(duration), platform);
    }
  };

  return (
    <form className="clips-form card" onSubmit={handleSubmit}>
      <label className="clips-form-field clips-form-platform">
        <span className="clips-label" id={platformId}>
          Platform
        </span>
        <select
          className="field"
          aria-labelledby={platformId}
          value={platform}
          onChange={(event) => setPlatform(event.target.value as ClipPlatform)}
        >
          {PLATFORM_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="clips-form-field clips-form-channel" htmlFor={channelId}>
        <span className="clips-label">Channel</span>
        <input
          id={channelId}
          className="field"
          type="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="channel name or https://www.twitch.tv/channel"
          value={channel}
          onChange={(event) => setChannel(event.target.value)}
        />
      </label>

      <div className="clips-form-field">
        <span className="clips-label">Save the last</span>
        <Segmented ariaLabel="Clip length" options={DURATION_OPTIONS} value={duration} onChange={setDuration} />
      </div>

      <button
        type="submit"
        className={`btn btn-accent clips-form-submit${isStarting ? ' is-busy' : ''}`}
        disabled={!canSubmit}
        aria-busy={isStarting}
      >
        {isStarting ? 'Starting…' : 'Save clip'}
      </button>
    </form>
  );
}
