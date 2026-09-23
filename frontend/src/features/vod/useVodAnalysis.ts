import React from 'react';

import { analyzeVod, labelVodPeaks, loadRecentVods, loadVod, loadVodActivity } from '../../api/vods';
import type { VodActivity, VodAnalysis, VodAnalysisJob, VodAnalysisResponse, VodJobStatus } from '../../types';

const POLL_INTERVAL_MS = 3000;
const RECENT_LIMIT = 10;

const ACTIVE_STATUSES: ReadonlySet<VodJobStatus> = new Set(['queued', 'fetching', 'ingesting', 'analyzing']);

export function isActiveVodStatus(status: string | null | undefined): boolean {
  return !!status && ACTIVE_STATUSES.has(status as VodJobStatus);
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export type UseVodAnalysis = {
  job: VodAnalysisJob | null;
  analysis: VodAnalysis | null;
  activity: VodActivity | null;
  recent: VodAnalysis[];
  isStarting: boolean;
  isLabeling: boolean;
  error: string;
  // Selected histogram bucket size; null means "use the analysis default".
  bucketSeconds: number | null;
  analyze: (video: string, force: boolean) => Promise<void>;
  label: () => Promise<void>;
  open: (videoId: string) => Promise<void>;
  setBucketSeconds: (seconds: number | null) => void;
};

export function useVodAnalysis(): UseVodAnalysis {
  const [job, setJob] = React.useState<VodAnalysisJob | null>(null);
  const [analysis, setAnalysis] = React.useState<VodAnalysis | null>(null);
  const [activity, setActivity] = React.useState<VodActivity | null>(null);
  const [recent, setRecent] = React.useState<VodAnalysis[]>([]);
  const [isStarting, setIsStarting] = React.useState(false);
  const [isLabeling, setIsLabeling] = React.useState(false);
  const [error, setError] = React.useState('');
  const [bucketSeconds, setBucketSeconds] = React.useState<number | null>(null);

  // Monotonic token so a slow response for a previously selected VOD can never
  // overwrite the one the user switched to afterwards.
  const selectionRef = React.useRef(0);
  const currentVideoRef = React.useRef<string | null>(null);

  const refreshRecent = React.useCallback(async () => {
    try {
      setRecent(await loadRecentVods(RECENT_LIMIT));
    } catch {
      // The recent list is secondary; keep whatever we already have.
    }
  }, []);

  const applyResponse = React.useCallback((response: VodAnalysisResponse) => {
    const videoId = response.job?.video_id ?? response.analysis?.video_id ?? null;
    if (videoId !== currentVideoRef.current) {
      currentVideoRef.current = videoId;
      setActivity(null);
      setBucketSeconds(null);
    }
    setJob(response.job);
    setAnalysis(response.analysis);
  }, []);

  React.useEffect(() => {
    void refreshRecent();
  }, [refreshRecent]);

  const analyze = React.useCallback(
    async (video: string, force: boolean) => {
      const trimmed = video.trim();
      if (!trimmed || isStarting) {
        return;
      }
      const token = ++selectionRef.current;
      setIsStarting(true);
      setError('');
      try {
        const response = await analyzeVod(trimmed, force);
        if (token === selectionRef.current) {
          applyResponse(response);
        }
      } catch (err) {
        if (token === selectionRef.current) {
          setError(errorMessage(err, 'Failed to start VOD analysis'));
        }
      } finally {
        setIsStarting(false);
      }
    },
    [applyResponse, isStarting]
  );

  const open = React.useCallback(
    async (videoId: string) => {
      const token = ++selectionRef.current;
      setError('');
      try {
        const response = await loadVod(videoId);
        if (token === selectionRef.current) {
          applyResponse(response);
        }
      } catch (err) {
        if (token === selectionRef.current) {
          setError(errorMessage(err, 'Failed to load VOD analysis'));
        }
      }
    },
    [applyResponse]
  );

  const videoId = analysis?.video_id ?? null;

  const label = React.useCallback(async () => {
    if (!videoId || isLabeling) {
      return;
    }
    setIsLabeling(true);
    setError('');
    try {
      const labeled = await labelVodPeaks(videoId);
      if (labeled.video_id === currentVideoRef.current) {
        setAnalysis(labeled);
      }
      void refreshRecent();
    } catch (err) {
      setError(errorMessage(err, 'Failed to label peaks'));
    } finally {
      setIsLabeling(false);
    }
  }, [isLabeling, refreshRecent, videoId]);

  // Poll the job while it is active. Keyed on id + status (not the whole job
  // object) so progress updates do not tear down and restart the interval.
  const jobVideoId = job?.video_id ?? null;
  const jobStatus = job?.status ?? null;

  React.useEffect(() => {
    if (!jobVideoId || !isActiveVodStatus(jobStatus)) {
      return;
    }

    let disposed = false;
    const interval = window.setInterval(async () => {
      try {
        const response = await loadVod(jobVideoId);
        if (!disposed && jobVideoId === currentVideoRef.current) {
          setJob(response.job);
          setAnalysis(response.analysis);
        }
      } catch (err) {
        if (!disposed) {
          setError(errorMessage(err, 'Failed to refresh VOD analysis status'));
        }
      }
    }, POLL_INTERVAL_MS);

    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, [jobVideoId, jobStatus]);

  // Refresh the recent list whenever a job reaches a terminal state.
  const previousJobStatus = React.useRef<string | null>(null);
  React.useEffect(() => {
    const previous = previousJobStatus.current;
    previousJobStatus.current = jobStatus;
    if (isActiveVodStatus(previous) && (jobStatus === 'completed' || jobStatus === 'failed')) {
      void refreshRecent();
    }
  }, [jobStatus, refreshRecent]);

  // Fetch the histogram once the analysis is complete, and again whenever the
  // bucket size changes or the analysis is re-run (updated_at moves).
  const analysisStatus = analysis?.status ?? null;
  const analysisUpdatedAt = analysis?.updated_at ?? null;

  React.useEffect(() => {
    if (!videoId || analysisStatus !== 'completed') {
      return;
    }

    let disposed = false;
    loadVodActivity(videoId, bucketSeconds)
      .then((result) => {
        if (!disposed) {
          setActivity(result);
        }
      })
      .catch((err) => {
        if (!disposed) {
          setError(errorMessage(err, 'Failed to load chat activity'));
        }
      });

    return () => {
      disposed = true;
    };
  }, [videoId, analysisStatus, analysisUpdatedAt, bucketSeconds]);

  return {
    job,
    analysis,
    activity,
    recent,
    isStarting,
    isLabeling,
    error,
    bucketSeconds,
    analyze,
    label,
    open,
    setBucketSeconds
  };
}
