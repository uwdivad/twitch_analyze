import React from 'react';

import { clipDownloadUrl } from '../../api/clips';
import type { ClipJob } from '../../types';
import { formatOffset } from '../../utils/format';
import { ClipForm } from './ClipForm';
import { isActiveClipStatus, readStoredChannel, useClipJobs } from './useClipJobs';
import './clips.css';

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000_000) {
    return `${(bytes / 1_000_000_000).toFixed(2)} GB`;
  }
  if (bytes >= 1_000_000) {
    return `${(bytes / 1_000_000).toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1000))} kB`;
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      }).format(date);
}

function channelName(job: ClipJob): string {
  return job.channel_display_name || job.channel_login;
}

function statusLabel(job: ClipJob): string {
  switch (job.status) {
    case 'queued':
      return 'queued';
    case 'resolving':
      return 'finding VOD';
    case 'downloading':
      return 'downloading';
    case 'completed':
      return 'saved';
    case 'failed':
      return 'failed';
    default:
      return job.status;
  }
}

function progressRatio(job: ClipJob): number | null {
  if (job.status !== 'downloading' || job.clip_duration_seconds <= 0) {
    return null;
  }
  return Math.min(1, Math.max(0, job.downloaded_seconds / job.clip_duration_seconds));
}

type ClipRowProps = {
  job: ClipJob;
  isDeleting: boolean;
  onDelete: (jobId: string) => void;
};

function ClipRow({ job, isDeleting, onDelete }: ClipRowProps) {
  const active = isActiveClipStatus(job.status);
  const ratio = progressRatio(job);
  const window =
    job.clip_end_seconds > 0 ? `${formatOffset(job.clip_start_seconds)} – ${formatOffset(job.clip_end_seconds)}` : '';
  const meta: string[] = [];
  if (job.video_title) {
    meta.push(job.video_title);
  }
  if (window) {
    meta.push(`${window} of the VOD`);
  }
  if (job.status === 'completed' && job.file_size_bytes > 0) {
    meta.push(formatBytes(job.file_size_bytes));
  }
  const when = formatDateTime(job.started_at);
  if (when) {
    meta.push(when);
  }

  return (
    <li className={`clips-row${active ? ' is-active' : ''}`}>
      <div className="clips-row-main">
        <div className="clips-row-head">
          <span className="clips-row-channel">{channelName(job)}</span>
          <span className={`pill pill-sm clips-status is-${job.status}`}>
            {active ? <span className="clips-status-dot" aria-hidden="true" /> : null}
            {statusLabel(job)}
          </span>
          <span className="muted clips-small">last {Math.round(job.requested_seconds / 60)} min</span>
        </div>
        {meta.length > 0 ? <p className="muted clips-small clips-row-meta">{meta.join(' · ')}</p> : null}
        {active ? (
          <>
            <div
              className={`clips-progress${ratio === null ? ' is-indeterminate' : ''}`}
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={ratio === null ? undefined : Math.round(ratio * 100)}
            >
              <div className="clips-progress-fill" style={ratio === null ? undefined : { width: `${ratio * 100}%` }} />
            </div>
            {job.detail ? (
              <p className="muted clips-small clips-mono">
                {job.detail}
                {ratio !== null
                  ? ` · ${formatOffset(job.downloaded_seconds)} / ${formatOffset(job.clip_duration_seconds)}`
                  : ''}
              </p>
            ) : null}
          </>
        ) : null}
        {job.status === 'failed' ? (
          <p className="clips-row-error" role="alert">
            {job.error || job.detail || 'Clip failed'}
          </p>
        ) : null}
      </div>
      <div className="clips-row-actions">
        {job.status === 'completed' ? (
          <a className="btn btn-primary btn-sm" href={clipDownloadUrl(job.job_id)} download={job.file_name || undefined}>
            Download
          </a>
        ) : null}
        <button
          type="button"
          className={`btn btn-ghost btn-sm${isDeleting ? ' is-busy' : ''}`}
          onClick={() => onDelete(job.job_id)}
          disabled={isDeleting}
          aria-busy={isDeleting}
        >
          {active ? 'Cancel' : 'Delete'}
        </button>
      </div>
    </li>
  );
}

// Memoized: App re-renders on every live-feed flush, and ClipsView takes no props.
export const ClipsView = React.memo(function ClipsView() {
  const clips = useClipJobs();
  const initialChannel = React.useMemo(readStoredChannel, []);

  const handleCreate = React.useCallback(
    (channel: string, durationMinutes: number, platform: 'twitch') => {
      void clips.create(channel, durationMinutes, platform);
    },
    [clips.create]
  );

  const handleDelete = React.useCallback(
    (jobId: string) => {
      void clips.remove(jobId);
    },
    [clips.remove]
  );

  return (
    <section className="clips-view">
      <header className="clips-hero">
        <h1>
          Clips <span className="muted">save the last few minutes of a live stream</span>
        </h1>
        <p className="muted clips-intro">
          Enter a channel that is live right now. The backend finds the VOD Twitch is recording for that stream and
          saves its last 5, 10 or 15 minutes as an MP4 you can download here.
        </p>
      </header>

      <ClipForm initialChannel={initialChannel} isStarting={clips.isStarting} onCreate={handleCreate} />

      {clips.error ? (
        <div className="clips-error" role="alert">
          {clips.error}
        </div>
      ) : null}

      <div className="clips-list card">
        <div className="clips-card-heading card-heading">
          <h3>Clips</h3>
          <span className="muted">{clips.jobs.length} total</span>
        </div>
        {clips.jobs.length === 0 ? (
          <p className="clips-empty muted">No clips yet. Saved clips stay listed here until you delete them.</p>
        ) : (
          <ul className="clips-rows">
            {clips.jobs.map((job) => (
              <ClipRow key={job.job_id} job={job} isDeleting={clips.deletingIds.has(job.job_id)} onDelete={handleDelete} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
});
