import React from 'react';
import { ChevronDown, Mic } from 'lucide-react';

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
  const [collapsed, setCollapsed] = React.useState(true);

  React.useEffect(() => {
    if (defaultChannel) {
      setChannel(defaultChannel);
    }
  }, [defaultChannel]);

  React.useEffect(() => {
    if (job || error) {
      setCollapsed(false);
    }
  }, [job, error]);

  const cleanChannel = channel.trim().replace(/^#/, '');
  const canStart = cleanChannel.length > 0 && !isStarting;

  return (
    <section className="card transcription-panel">
      <div className="card-heading transcription-heading">
        <div className="card-title">
          <h2>Streamer audio</h2>
          <span className="card-meta">{job ? `${job.channel_login} · ${job.status}` : 'Timed live transcription'}</span>
        </div>
        <div className="card-actions summary-actions">
          {job ? <span className="transcription-window">{formatJobWindow(job)}</span> : null}
          <button
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand transcription controls' : 'Collapse transcription controls'}
            className={`btn btn-ghost btn-icon collapse-toggle${collapsed ? ' is-collapsed' : ''}`}
            onClick={() => setCollapsed((current) => !current)}
            type="button"
          >
            <ChevronDown size={16} />
          </button>
        </div>
      </div>
      {collapsed ? null : (
        <>
          {error ? <div className="alert summary-error">{error}</div> : null}
          <form
            className="transcription-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (canStart) {
                onStart(cleanChannel, durationMinutes);
              }
            }}
          >
            <label className="field-label">
              <span>Channel</span>
              <input
                className="field"
                autoComplete="off"
                inputMode="text"
                pattern="[A-Za-z0-9_]+"
                placeholder="twitch channel"
                value={channel}
                onChange={(event) => setChannel(event.target.value)}
              />
            </label>
            <label className="field-label">
              <span>Transcribe for</span>
              <select
                className="field"
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
            <button
              className={`btn btn-primary${isStarting ? ' is-busy' : ''}`}
              disabled={!canStart}
              type="submit"
            >
              <Mic size={16} />
              {isStarting ? 'Starting' : 'Start'}
            </button>
          </form>
          {job ? <div className="transcription-status">{job.detail || statusText(job)}</div> : null}
        </>
      )}
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
