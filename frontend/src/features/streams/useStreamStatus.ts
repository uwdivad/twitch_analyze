import React from 'react';

import { lookupStreamStatus, type StreamHandleOverrides } from '../../api/streams';
import type { StreamPlatform, StreamStatusResponse } from '../../types';

// How often the open lookup is re-checked; the backend caches answers for a shorter time.
export const POLL_INTERVAL_MS = 60_000;
const MAX_POLL_FAILURES = 5;
// Remembers the open lookup across view switches (StreamsView unmounts on other views).
const STORAGE_KEY = 'twitch-analyze-streams';

export type StreamQuery = {
  name: string;
  overrides: StreamHandleOverrides;
};

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

function isQuery(value: unknown): value is StreamQuery {
  if (!value || typeof value !== 'object') {
    return false;
  }
  const candidate = value as Partial<StreamQuery>;
  return typeof candidate.name === 'string' && (!candidate.overrides || typeof candidate.overrides === 'object');
}

export function readStoredQuery(): StreamQuery | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isQuery(parsed) && parsed.name.trim() ? { name: parsed.name, overrides: parsed.overrides ?? {} } : null;
  } catch {
    return null;
  }
}

function writeStoredQuery(query: StreamQuery | null): void {
  try {
    if (query) {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(query));
    } else {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable (private mode, blocked site data); persistence is a convenience.
  }
}

export type UseStreamStatus = {
  query: StreamQuery | null;
  response: StreamStatusResponse | null;
  // True while the first answer for the current query is still loading.
  isLoading: boolean;
  // True while any request for the current query is in flight (initial or refresh).
  isRefreshing: boolean;
  error: string;
  search: (name: string) => void;
  setOverride: (platform: StreamPlatform, handle: string) => void;
  refresh: () => void;
  clear: () => void;
};

export function useStreamStatus(): UseStreamStatus {
  const [query, setQuery] = React.useState<StreamQuery | null>(readStoredQuery);
  const [response, setResponse] = React.useState<StreamStatusResponse | null>(null);
  const [isRefreshing, setIsRefreshing] = React.useState(false);
  const [error, setError] = React.useState('');
  // Bumped by refresh() to re-run the fetch effect without changing the query.
  const [refreshTick, setRefreshTick] = React.useState(0);

  React.useEffect(() => {
    writeStoredQuery(query);
  }, [query]);

  // Primitive key so override edits re-run the effect but unrelated renders do not.
  const queryKey = query ? JSON.stringify([query.name, query.overrides]) : '';

  React.useEffect(() => {
    if (!query) {
      setResponse(null);
      setError('');
      return;
    }
    const current = query;
    let disposed = false;
    let timer: number | null = null;
    let inFlight = false;
    let failures = 0;
    let lastFetchedAt = 0;

    const schedule = (delay: number) => {
      if (timer !== null) {
        window.clearTimeout(timer);
      }
      timer = window.setTimeout(() => {
        timer = null;
        void fetchStatus();
      }, delay);
    };

    const fetchStatus = async () => {
      if (disposed || inFlight) {
        return;
      }
      // Pause while the tab is hidden; visibilitychange below resumes it.
      if (document.visibilityState === 'hidden') {
        return;
      }
      inFlight = true;
      setIsRefreshing(true);
      try {
        const next = await lookupStreamStatus(current.name, current.overrides);
        if (disposed) {
          return;
        }
        failures = 0;
        lastFetchedAt = Date.now();
        setResponse(next);
        setError('');
      } catch (err) {
        if (disposed) {
          return;
        }
        failures += 1;
        setError(errorMessage(err, 'Failed to check live status'));
        if (failures >= MAX_POLL_FAILURES) {
          return;
        }
      } finally {
        inFlight = false;
        if (!disposed) {
          setIsRefreshing(false);
        }
      }
      if (!disposed) {
        schedule(POLL_INTERVAL_MS);
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState !== 'visible' || disposed || inFlight) {
        return;
      }
      const age = Date.now() - lastFetchedAt;
      // Catch up right away if the last answer is stale; otherwise wait out the interval.
      schedule(age >= POLL_INTERVAL_MS ? 0 : POLL_INTERVAL_MS - age);
    };
    document.addEventListener('visibilitychange', handleVisibility);

    void fetchStatus();

    return () => {
      disposed = true;
      document.removeEventListener('visibilitychange', handleVisibility);
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
    // queryKey covers the query contents; refreshTick forces a re-run on demand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryKey, refreshTick]);

  const search = React.useCallback((name: string) => {
    const trimmed = name.trim();
    if (!trimmed) {
      return;
    }
    setQuery((previous) => {
      // Overrides belong to the previous streamer; keep them only when re-searching the same name.
      const sameName = previous?.name.trim().toLowerCase() === trimmed.toLowerCase();
      return { name: trimmed, overrides: sameName && previous ? previous.overrides : {} };
    });
    setResponse(null);
    setError('');
  }, []);

  const setOverride = React.useCallback((platform: StreamPlatform, handle: string) => {
    setQuery((previous) => {
      if (!previous) {
        return previous;
      }
      const overrides: StreamHandleOverrides = { ...previous.overrides };
      const trimmed = handle.trim();
      if (trimmed) {
        overrides[platform] = trimmed;
      } else {
        delete overrides[platform];
      }
      return { ...previous, overrides };
    });
  }, []);

  const refresh = React.useCallback(() => {
    setRefreshTick((tick) => tick + 1);
  }, []);

  const clear = React.useCallback(() => {
    setQuery(null);
    setResponse(null);
    setError('');
  }, []);

  return {
    query,
    response,
    isLoading: !!query && response === null && !error,
    isRefreshing,
    error,
    search,
    setOverride,
    refresh,
    clear
  };
}
