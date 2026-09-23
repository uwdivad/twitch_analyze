import React from 'react';

import type { VodAnalysis, VodAnalysisJob } from '../../types';
import { formatOffset } from '../../utils/format';
import { VodActivityBar } from './VodActivityBar';
import { VodForm } from './VodForm';
import { VodPeakList } from './VodPeakList';
import { VodPlayer, type VodPlayerHandle } from './VodPlayer';
import { isActiveVodStatus, useVodAnalysis } from './useVodAnalysis';
import './vod.css';

const BUCKET_OPTIONS = [5, 10, 15, 30, 60, 120, 300];

function jobProgress(job: VodAnalysisJob): { text: string; ratio: number | null } {
  switch (job.status) {
    case 'queued':
      return { text: 'queued: waiting for a free worker…', ratio: null };
    case 'fetching': {
      const ratio = job.duration_seconds > 0 ? job.last_offset_seconds / job.duration_seconds : null;
      return {
        text: `fetching: fetched ${job.fetched_comments.toLocaleString()} comments · ${formatOffset(
          job.last_offset_seconds
        )} / ${formatOffset(job.duration_seconds)}`,
        ratio
      };
    }
    case 'ingesting':
      return {
        text: `ingesting: stored ${job.stored_comments.toLocaleString()} / ${job.fetched_comments.toLocaleString()}`,
        ratio: job.fetched_comments > 0 ? job.stored_comments / job.fetched_comments : null
      };
    case 'analyzing':
      return { text: 'analyzing: detecting peaks…', ratio: null };
    default:
      return { text: job.status, ratio: null };
  }
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' }).format(date);
}

function channelName(analysis: VodAnalysis): string {
  return analysis.channel_display_name || analysis.channel_login || 'unknown channel';
}

export function VodView() {
  const vod = useVodAnalysis();
  const { job, analysis, activity } = vod;
  const playerRef = React.useRef<VodPlayerHandle | null>(null);
  const [currentTime, setCurrentTime] = React.useState(0);

  const handleSeek = React.useCallback((seconds: number) => {
    // Before the player is READY the seek is dropped, so leave the playhead
    // where the player actually is instead of jumping it.
    if (playerRef.current?.seek(seconds)) {
      setCurrentTime(seconds);
    }
  }, []);

  const handleAnalyze = React.useCallback(
    (video: string, force: boolean) => {
      void vod.analyze(video, force);
    },
    [vod.analyze]
  );

  const videoId = analysis?.video_id ?? null;
  React.useEffect(() => {
    setCurrentTime(0);
  }, [videoId]);

  const isActive = isActiveVodStatus(job?.status);
  const progress = job && isActive ? jobProgress(job) : null;
  const jobError = job?.status === 'failed' ? job.error || job.detail || 'VOD analysis failed' : '';
  const analysisFailed = analysis?.status === 'failed';
  const showAnalysis = !!analysis && !analysisFailed;
  // Prefer what the backend actually returned: it may clamp the requested size.
  const selectedBucket = activity?.bucket_seconds ?? vod.bucketSeconds ?? analysis?.bucket_seconds ?? 0;
  // Sizes below this would produce more than ~3600 buckets; the backend rejects or clamps them.
  const minBucketSeconds = Math.ceil((analysis?.duration_seconds ?? 0) / 3600);
  const bucketOptions = selectedBucket && !BUCKET_OPTIONS.includes(selectedBucket)
    ? [...BUCKET_OPTIONS, selectedBucket].sort((a, b) => a - b)
    : BUCKET_OPTIONS;

  return (
    <section className="vod-view">
      <header className="vod-hero">
        <h1>
          VOD analysis <span className="muted">find the moments chat went wild</span>
        </h1>
      </header>

      <VodForm isStarting={vod.isStarting} onAnalyze={handleAnalyze} />

      {progress && job ? (
        <div className="vod-status card" role="status" aria-live="polite">
          <div className="vod-status-line">
            <span className="vod-status-dot" aria-hidden="true" />
            <span className="vod-mono">{progress.text}</span>
          </div>
          <div
            className={`vod-progress${progress.ratio === null ? ' is-indeterminate' : ''}`}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress.ratio === null ? undefined : Math.round(Math.min(1, progress.ratio) * 100)}
          >
            <div
              className="vod-progress-fill"
              style={progress.ratio === null ? undefined : { width: `${Math.min(1, Math.max(0, progress.ratio)) * 100}%` }}
            />
          </div>
          {job.detail ? <p className="vod-status-detail muted">{job.detail}</p> : null}
        </div>
      ) : null}

      {vod.error ? (
        <div className="vod-error" role="alert">
          {vod.error}
        </div>
      ) : null}
      {jobError ? (
        <div className="vod-error" role="alert">
          Import failed: {jobError}
        </div>
      ) : null}
      {analysisFailed && analysis ? (
        <div className="vod-error" role="alert">
          Analysis failed: {analysis.error || 'unknown error'}
        </div>
      ) : null}

      {showAnalysis && analysis ? (
        <>
          <div className="vod-meta">
            <h2 className="vod-title">{analysis.title || `VOD ${analysis.video_id}`}</h2>
            <p className="muted">
              {channelName(analysis)}
              {analysis.video_created_at ? ` · ${formatDate(analysis.video_created_at)}` : ''}
              {analysis.duration_seconds ? ` · ${formatOffset(analysis.duration_seconds)}` : ''}
            </p>
          </div>

          <div className="vod-stage card">
            <VodPlayer ref={playerRef} videoId={analysis.video_id} onTimeUpdate={setCurrentTime} />
            <VodActivityBar
              activity={activity}
              peaks={analysis.peaks}
              currentTime={currentTime}
              durationSeconds={analysis.duration_seconds}
              onSeek={handleSeek}
            />
          </div>

          <div className="vod-columns">
            <div className="vod-peaks card">
              <div className="vod-card-heading card-heading">
                <h3>Chat peaks</h3>
                <span className="muted">{analysis.peaks.length} found</span>
              </div>
              {analysis.peaks.length > 0 ? (
                <VodPeakList peaks={analysis.peaks} currentTime={currentTime} onSeek={handleSeek} />
              ) : (
                <p className="vod-empty muted">
                  {analysis.status === 'completed'
                    ? 'No chat peaks were detected in this VOD. Chat stayed close to its baseline the whole time.'
                    : 'Peaks appear here once the analysis finishes.'}
                </p>
              )}
            </div>

            <aside className="vod-side card">
              <div className="vod-stats">
                <div className="stat vod-stat">
                  <span className="vod-stat-label">Messages</span>
                  <strong className="vod-stat-value">{analysis.message_count.toLocaleString()}</strong>
                </div>
                <div className="stat vod-stat">
                  <span className="vod-stat-label">Unique chatters</span>
                  <strong className="vod-stat-value">{analysis.unique_chatter_count.toLocaleString()}</strong>
                </div>
                <div className="stat vod-stat">
                  <span className="vod-stat-label">Peaks</span>
                  <strong className="vod-stat-value">{analysis.peaks.length.toLocaleString()}</strong>
                </div>
              </div>

              <label className="vod-side-field">
                <span className="vod-stat-label">Bucket size</span>
                <select
                  className="field"
                  value={selectedBucket || ''}
                  onChange={(event) => vod.setBucketSeconds(Number(event.target.value))}
                >
                  {bucketOptions.map((seconds) => (
                    <option key={seconds} value={seconds} disabled={seconds < minBucketSeconds}>
                      {seconds < 60 ? `${seconds} s` : `${seconds / 60} min`}
                      {seconds === analysis.bucket_seconds ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
              </label>

              <div className="vod-label-action">
                <button
                  type="button"
                  className={`btn btn-primary${vod.isLabeling ? ' is-busy' : ''}`}
                  onClick={() => void vod.label()}
                  disabled={vod.isLabeling || analysis.peaks.length === 0 || analysis.status !== 'completed'}
                  aria-busy={vod.isLabeling}
                >
                  {vod.isLabeling ? 'Labeling…' : 'Label peaks with AI'}
                </button>
                {analysis.label_model ? (
                  <span className="muted vod-small">Labeled by {analysis.label_model}</span>
                ) : null}
              </div>

              <RecentList recent={vod.recent} activeId={analysis.video_id} onOpen={vod.open} />
            </aside>
          </div>
        </>
      ) : (
        <div className="vod-idle">
          <RecentList recent={vod.recent} activeId={null} onOpen={vod.open} />
        </div>
      )}
    </section>
  );
}

type RecentListProps = {
  recent: VodAnalysis[];
  activeId: string | null;
  onOpen: (videoId: string) => Promise<void>;
};

function RecentList({ recent, activeId, onOpen }: RecentListProps) {
  return (
    <div className="vod-recent">
      <h3 className="vod-stat-label">Recent analyses</h3>
      {recent.length === 0 ? (
        <p className="muted vod-small">No VODs analyzed yet.</p>
      ) : (
        <ul>
          {recent.map((item) => (
            <li key={item.video_id}>
              <button
                type="button"
                className={`vod-recent-row${item.video_id === activeId ? ' is-active' : ''}`}
                onClick={() => void onOpen(item.video_id)}
              >
                <span className="vod-recent-title">{item.title || `VOD ${item.video_id}`}</span>
                <span className="muted vod-small">
                  {channelName(item)}
                  {item.video_created_at ? ` · ${formatDate(item.video_created_at)}` : ''}
                  {` · ${(item.peaks?.length ?? 0).toLocaleString()} peaks`}
                  {item.status !== 'completed' ? ` · ${item.status}` : ''}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
