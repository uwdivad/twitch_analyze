import React from 'react';

import type { PlatformStatus, StreamPlatform } from '../../types';
import { formatTime } from '../../utils/format';
import { PLATFORMS, PlatformCard } from './PlatformCard';
import { StreamSearchForm } from './StreamSearchForm';
import { POLL_INTERVAL_MS, useStreamStatus } from './useStreamStatus';
import './streams.css';

type StreamsViewProps = {
  // Feature flag from Settings; the backend rejects lookups when off.
  enabled: boolean;
};

// Memoized: App re-renders on every live-feed flush; the only prop is a boolean.
export const StreamsView = React.memo(function StreamsView({ enabled }: StreamsViewProps) {
  const streams = useStreamStatus();
  const { query, response } = streams;
  const initialName = React.useMemo(() => query?.name ?? '', [query?.name]);

  const byPlatform = React.useMemo(() => {
    const map = new Map<StreamPlatform, PlatformStatus>();
    for (const status of response?.platforms ?? []) {
      map.set(status.platform, status);
    }
    return map;
  }, [response]);

  const liveCount = response ? response.platforms.filter((status) => status.state === 'live').length : 0;

  return (
    <section className="streams-view">
      <header className="streams-hero">
        <h1>
          Streams <span className="muted">is this streamer live anywhere?</span>
        </h1>
        <p className="muted streams-intro">
          Enter an account name to check Twitch, Kick and YouTube at once. Live channels play here (muted); each
          platform can use its own handle if they differ.
        </p>
      </header>

      {enabled ? (
        <StreamSearchForm key={initialName} initialName={initialName} isBusy={streams.isLoading} onSearch={streams.search} />
      ) : (
        <p className="muted streams-small">
          Streamer live status is turned off in <a href="#settings">Settings</a>.
        </p>
      )}

      {streams.error ? (
        <div className="streams-error" role="alert">
          {streams.error}
        </div>
      ) : null}

      {query && enabled ? (
        <>
          <div className="streams-status-line" role="status" aria-live="polite">
            <span className="streams-mono">
              {response
                ? `${response.name}: live on ${liveCount} of ${response.platforms.length} · checked ${formatTime(
                    response.checked_at
                  )}`
                : `Checking ${query.name}…`}
            </span>
            <span className="muted streams-small">
              refreshes every {Math.round(POLL_INTERVAL_MS / 1000)} s
            </span>
            <div className="streams-status-actions">
              <button
                type="button"
                className={`btn btn-sm btn-ghost${streams.isRefreshing ? ' is-busy' : ''}`}
                onClick={streams.refresh}
                disabled={streams.isRefreshing}
                aria-busy={streams.isRefreshing}
              >
                {streams.isRefreshing ? 'Refreshing…' : 'Refresh'}
              </button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={streams.clear}>
                Clear
              </button>
            </div>
          </div>

          <div className="streams-grid">
            {PLATFORMS.map((platform) => (
              <PlatformCard
                key={platform.id}
                platform={platform.id}
                status={byPlatform.get(platform.id) ?? null}
                override={query.overrides[platform.id]}
                isRefreshing={streams.isRefreshing}
                onOverride={streams.setOverride}
                onRetry={streams.refresh}
              />
            ))}
          </div>
        </>
      ) : enabled ? (
        <div className="empty streams-empty">
          Try a name like <code>lofigirl</code>. Each platform gets its own card with the live player when the
          streamer is online.
        </div>
      ) : null}
    </section>
  );
});
