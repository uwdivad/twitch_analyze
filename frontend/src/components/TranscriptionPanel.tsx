import React from 'react';
import { Mic } from 'lucide-react';

import type { TranscriptionJob } from '../types';

type TranscriptionPanelProps = {
  defaultChannel: string;
  job: TranscriptionJob | null;
  isStarting: boolean;
  error: string;
  onStart: (channel: string, durationMinutes: number) => void;
};

const DURATION_OPTIONS = [5, 10, 15, 30, 60];

export function TranscriptionPanel({
  defaultChannel,
  job,
  isStarting,
  error,
  onStart
}: TranscriptionPanelProps) {
  const [channel, setChannel] = React.useState(defaultChannel);
  const [durationMinutes, setDurationMinutes] = React.useState(10);

  React.useEffect(() => {
    if (defaultChannel) {
      setChannel(defaultChannel);
    }
  }, [defaultChannel]);

  const cleanChannel = channel.trim().replace(/^#/, '');
  const canStart = cleanChannel.length > 0 && !isStarting;

  return (
    <section className="panel transcription-panel">
      <div className="panel-heading transcription-heading">
        <div>
          <h2>Streamer Audio</h2>
          <span>{job ? `${job.channel_login} · ${job.status}` : 'Timed live transcription'}</span>
        </div>
        {job ? <span>{formatJobWindow(job)}</span> : null}
      </div>
      {error ? <div className="summary-error">{error}</div> : null}
      <form
        className="transcription-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canStart) {
            onStart(cleanChannel, durationMinutes);
          }
        }}
      >
        <label>
          <span>Channel</span>
          <input
            autoComplete="off"
            inputMode="text"
            pattern="[A-Za-z0-9_]+"
            placeholder="twitch channel"
            value={channel}
            onChange={(event) => setChannel(event.target.value)}
          />
        </label>
        <label>
          <span>Transcribe for</span>
          <select
            value={durationMinutes}
            onChange={(event) => setDurationMinutes(Number(event.target.value))}
          >
            {DURATION_OPTIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes} minutes
              </option>
            ))}
          </select>
        </label>
        <button disabled={!canStart} type="submit">
          <Mic size={16} />
          {isStarting ? 'Starting' : 'Start'}
        </button>
      </form>
      {job ? <div className="transcription-status">{job.detail || statusText(job)}</div> : null}
    </section>
  );
}

function statusText(job: TranscriptionJob): string {
  if (job.status === 'running') {
    return `Capturing until ${new Date(job.ends_at).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit'
    })}`;
  }
  return job.status;
}

function formatJobWindow(job: TranscriptionJob): string {
  const minutes = Math.round(job.duration_seconds / 60);
  return `${minutes}m`;
}
