import React from 'react';

import { createClip, deleteClip, loadClips } from '../../api/clips';
import type { ClipJob, ClipJobStatus, ClipPlatform } from '../../types';

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_FAILURES = 5;
const LIST_LIMIT = 50;
// Remembers the last channel typed so a reload or view switch keeps it.
const CHANNEL_STORAGE_KEY = 'twitch-analyze-clip-channel';

const ACTIVE_STATUSES: ReadonlySet<ClipJobStatus> = new Set(['queued', 'resolving', 'downloading']);

export function isActiveClipStatus(status: string | null | undefined): boolean {
  return !!status && ACTIVE_STATUSES.has(status as ClipJobStatus);
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export function readStoredChannel(): string {
  try {
    return window.localStorage.getItem(CHANNEL_STORAGE_KEY) ?? '';
  } catch {
    return '';
  }
}

function writeStoredChannel(channel: string): void {
  try {
    window.localStorage.setItem(CHANNEL_STORAGE_KEY, channel);
  } catch {
    // Storage can be unavailable (private mode, blocked site data); persistence is a convenience.
  }
}

export type UseClipJobs = {
  jobs: ClipJob[];
  isStarting: boolean;
  deletingIds: ReadonlySet<string>;
  error: string;
  create: (channel: string, durationMinutes: number, platform?: ClipPlatform) => Promise<void>;
  remove: (jobId: string) => Promise<void>;
  refresh: () => Promise<void>;
};

export function useClipJobs(): UseClipJobs {
  const [jobs, setJobs] = React.useState<ClipJob[]>([]);
  const [isStarting, setIsStarting] = React.useState(false);
  const [deletingIds, setDeletingIds] = React.useState<ReadonlySet<string>>(() => new Set());
  const [error, setError] = React.useState('');

  const refresh = React.useCallback(async () => {
    const next = await loadClips(LIST_LIMIT);
    setJobs(next);
  }, []);

  React.useEffect(() => {
    refresh().catch((err) => setError(errorMessage(err, 'Failed to load clips')));
  }, [refresh]);

  const create = React.useCallback(
    async (channel: string, durationMinutes: number, platform: ClipPlatform = 'twitch') => {
      const trimmed = channel.trim();
      if (!trimmed || isStarting) {
        return;
      }
      setIsStarting(true);
      setError('');
      try {
        const job = await createClip(trimmed, durationMinutes, platform);
        writeStoredChannel(trimmed);
        setJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)]);
      } catch (err) {
        setError(errorMessage(err, 'Failed to start the clip'));
      } finally {
        setIsStarting(false);
      }
    },
    [isStarting]
  );

  const remove = React.useCallback(async (jobId: string) => {
    setDeletingIds((current) => new Set(current).add(jobId));
    setError('');
    try {
      await deleteClip(jobId);
      setJobs((current) => current.filter((item) => item.job_id !== jobId));
    } catch (err) {
      setError(errorMessage(err, 'Failed to delete the clip'));
    } finally {
      setDeletingIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
    }
  }, []);

  // Poll while any job is active. Each poll is scheduled only after the previous
  // request settles, so requests never overlap. Keyed on the active id set so
  // progress updates do not restart the loop.
  const activeKey = jobs
    .filter((job) => isActiveClipStatus(job.status))
    .map((job) => job.job_id)
    .sort()
    .join(',');

  React.useEffect(() => {
    if (!activeKey) {
      return;
    }

    let disposed = false;
    let timer: number | null = null;
    let failures = 0;

    const poll = async () => {
      timer = null;
      try {
        const next = await loadClips(LIST_LIMIT);
        if (disposed) {
          return;
        }
        failures = 0;
        setJobs(next);
      } catch (err) {
        if (disposed) {
          return;
        }
        failures += 1;
        if (failures >= MAX_POLL_FAILURES) {
          setError(`Lost contact with the backend: ${errorMessage(err, 'request failed')}`);
          return;
        }
      }
      if (!disposed) {
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    timer = window.setTimeout(poll, POLL_INTERVAL_MS);

    return () => {
      disposed = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [activeKey]);

  return { jobs, isStarting, deletingIds, error, create, remove, refresh };
}
