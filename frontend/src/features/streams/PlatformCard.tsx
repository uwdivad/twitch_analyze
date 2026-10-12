import React from 'react';

import type { PlatformStatus, StreamPlatform } from '../../types';
import { IframePlayer, kickEmbedUrl, youtubeEmbedUrl } from './players/IframePlayer';
import { TwitchChannelPlayer } from './players/TwitchChannelPlayer';

export const PLATFORMS: ReadonlyArray<{ id: StreamPlatform; label: string; handlePrefix: string }> = [
  { id: 'twitch', label: 'Twitch', handlePrefix: 'twitch.tv/' },
  { id: 'kick', label: 'Kick', handlePrefix: 'kick.com/' },
  { id: 'youtube', label: 'YouTube', handlePrefix: 'youtube.com/@' }
];

const viewerFormat = new Intl.NumberFormat(undefined);

function formatViewers(count: number | null): string {
  return count === null ? '' : `${viewerFormat.format(count)} watching`;
}

function formatUptime(startedAt: string | null, now: number): string {
  if (!startedAt) {
    return '';
  }
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started) || started > now) {
    return '';
  }
  const minutes = Math.floor((now - started) / 60_000);
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `live for ${hours}h ${String(minutes % 60).padStart(2, '0')}m` : `live for ${minutes}m`;
}

function stateLabel(status: PlatformStatus): string {
  switch (status.state) {
    case 'live':
      return 'Live';
    case 'offline':
      return status.detail === 'Scheduled stream' ? 'Scheduled' : 'Offline';
    case 'not_found':
      return 'Not found';
    default:
      return 'Unavailable';
  }
}

function Player({ status }: { status: PlatformStatus }) {
  // Keys tie the embed to the stream identity, so a 60 s status refresh that
  // returns the same stream leaves the iframe alone.
  switch (status.platform) {
    case 'twitch':
      return <TwitchChannelPlayer key={status.handle} channel={status.handle} />;
    case 'kick':
      return <IframePlayer key={status.handle} src={kickEmbedUrl(status.handle)} title={`${status.display_name} on Kick`} />;
    case 'youtube':
      return (
        <IframePlayer
          key={status.video_id}
          src={youtubeEmbedUrl(status.video_id)}
          title={`${status.display_name} on YouTube`}
        />
      );
    default:
      return null;
  }
}

type HandleEditorProps = {
  platform: StreamPlatform;
  current: string;
  isOverridden: boolean;
  onApply: (handle: string) => void;
  onClose: () => void;
};

function HandleEditor({ platform, current, isOverridden, onApply, onClose }: HandleEditorProps) {
  const [value, setValue] = React.useState(current);
  const inputId = React.useId();
  const meta = PLATFORMS.find((item) => item.id === platform);

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onApply(value.trim());
    onClose();
  };

  return (
    <form className="streams-handle-editor" onSubmit={handleSubmit}>
      <label className="streams-visually-hidden" htmlFor={inputId}>
        {meta?.label ?? platform} handle
      </label>
      <input
        id={inputId}
        className="field streams-handle-input"
        type="text"
        autoComplete="off"
        spellCheck={false}
        autoFocus
        placeholder={`${meta?.handlePrefix ?? ''}…`}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            onClose();
          }
        }}
      />
      <button type="submit" className="btn btn-sm btn-primary">
        Apply
      </button>
      {isOverridden ? (
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={() => {
            onApply('');
            onClose();
          }}
        >
          Use main name
        </button>
      ) : null}
      <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>
        Cancel
      </button>
    </form>
  );
}

type PlatformCardProps = {
  platform: StreamPlatform;
  // Null while the first lookup for this query is loading.
  status: PlatformStatus | null;
  // The handle override typed for this platform, if any.
  override: string | undefined;
  // Stale: a refresh is in flight for a response we already show.
  isRefreshing: boolean;
  onOverride: (platform: StreamPlatform, handle: string) => void;
  onRetry: () => void;
};

export function PlatformCard({ platform, status, override, isRefreshing, onOverride, onRetry }: PlatformCardProps) {
  const meta = PLATFORMS.find((item) => item.id === platform) ?? PLATFORMS[0];
  const [isEditing, setIsEditing] = React.useState(false);
  const [now, setNow] = React.useState(() => Date.now());
  const isLive = status?.state === 'live';

  // Tick the uptime once a minute while live.
  React.useEffect(() => {
    if (!isLive) {
      return;
    }
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, [isLive, status?.started_at]);

  const handleApply = React.useCallback((handle: string) => onOverride(platform, handle), [onOverride, platform]);
  const closeEditor = React.useCallback(() => setIsEditing(false), []);

  const handleShown = status?.handle || override || '';
  const dotClass = status ? (isLive ? 'is-live' : status.state === 'error' ? 'is-connecting' : 'is-offline') : '';

  return (
    <article className={`card streams-card is-${platform}${isLive ? ' is-live' : ''}`} aria-busy={!status}>
      <header className="streams-card-head">
        <div className="streams-card-title">
          <span className="streams-brand-dot" aria-hidden="true" />
          <h2>{meta.label}</h2>
          {status ? (
            <span className={`pill pill-sm streams-state is-${status.state}`}>
              <span className={`dot ${dotClass}`} aria-hidden="true" />
              {stateLabel(status)}
            </span>
          ) : (
            <span className="pill pill-sm streams-state">
              <span className="dot is-connecting" aria-hidden="true" />
              Checking
            </span>
          )}
        </div>
        <div className="streams-card-handle">
          {status?.channel_url ? (
            <a className="streams-handle-link" href={status.channel_url} target="_blank" rel="noreferrer noopener">
              {meta.handlePrefix}
              {handleShown}
            </a>
          ) : (
            <span className="streams-handle-link muted">
              {meta.handlePrefix}
              {handleShown}
            </span>
          )}
          {override ? <span className="streams-override-tag">custom</span> : null}
          <button
            type="button"
            className="btn btn-sm btn-ghost streams-edit-btn"
            onClick={() => setIsEditing((open) => !open)}
            aria-expanded={isEditing}
          >
            {isEditing ? 'Close' : 'Edit'}
          </button>
        </div>
        {isEditing ? (
          <HandleEditor
            platform={platform}
            current={override ?? status?.handle ?? ''}
            isOverridden={!!override}
            onApply={handleApply}
            onClose={closeEditor}
          />
        ) : null}
      </header>

      {!status ? (
        <div className="streams-player streams-player-placeholder skeleton" aria-label="Checking live status" />
      ) : status.state === 'live' ? (
        <>
          <Player status={status} />
          <div className="streams-live-meta">
            {status.title ? <p className="streams-stream-title">{status.title}</p> : null}
            <p className="muted streams-small">
              {[
                status.display_name,
                status.category,
                formatViewers(status.viewer_count),
                formatUptime(status.started_at, now)
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          </div>
        </>
      ) : status.state === 'offline' ? (
        <div className="streams-offline">
          {status.avatar_url || status.thumbnail_url ? (
            <img
              className={status.avatar_url ? 'streams-avatar' : 'streams-offline-thumb'}
              src={status.avatar_url || status.thumbnail_url}
              alt=""
              loading="lazy"
            />
          ) : (
            <span className="streams-avatar streams-avatar-fallback" aria-hidden="true">
              {(status.display_name || status.handle).slice(0, 1).toUpperCase()}
            </span>
          )}
          <div className="streams-offline-text">
            <p className="streams-offline-name">{status.display_name || status.handle}</p>
            <p className="muted streams-small">
              {status.detail === 'Scheduled stream' && status.title
                ? `Scheduled: ${status.title}`
                : status.detail || `Not live on ${meta.label} right now.`}
            </p>
            {status.channel_url ? (
              <a className="btn btn-sm btn-ghost" href={status.channel_url} target="_blank" rel="noreferrer noopener">
                Open channel
              </a>
            ) : null}
          </div>
        </div>
      ) : status.state === 'not_found' ? (
        <div className="empty streams-empty-state">
          <p>{status.detail || `No ${meta.label} channel named ${status.handle}.`}</p>
          <p className="streams-small">Different handle here? Use Edit to set one for {meta.label} only.</p>
        </div>
      ) : (
        <div className="alert streams-alert" role="alert">
          <p>
            {meta.label} could not be checked{status.detail ? `: ${status.detail}` : '.'}
          </p>
          <button type="button" className="btn btn-sm btn-ghost" onClick={onRetry} disabled={isRefreshing}>
            Retry
          </button>
        </div>
      )}
    </article>
  );
}
