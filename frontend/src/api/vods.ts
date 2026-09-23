import type { VodActivity, VodAnalysis, VodAnalysisResponse } from '../types';
import { getJson, postJson } from './client';

// AI labeling calls OpenAI synchronously on the backend, so it gets a much
// longer timeout than the default request budget.
const LABEL_TIMEOUT_MS = 90_000;

function vodPath(videoId: string): string {
  return `/api/vods/${encodeURIComponent(videoId)}`;
}

// TODO(integration): postJson does not attach an X-API-Key header today. If
// API_AUTH_TOKEN is set on the backend, these POSTs are rejected exactly like
// the existing summary/transcription POSTs until client.ts learns to send it.
export async function analyzeVod(video: string, force = false): Promise<VodAnalysisResponse> {
  return postJson<VodAnalysisResponse>('/api/vods/analyze', { video, force });
}

export async function loadVod(videoId: string): Promise<VodAnalysisResponse> {
  return getJson<VodAnalysisResponse>(vodPath(videoId));
}

export async function loadVodActivity(videoId: string, bucketSeconds?: number | null): Promise<VodActivity> {
  const query = bucketSeconds ? `?bucket_seconds=${bucketSeconds}` : '';
  return getJson<VodActivity>(`${vodPath(videoId)}/activity${query}`);
}

export async function labelVodPeaks(videoId: string): Promise<VodAnalysis> {
  return postJson<VodAnalysis>(`${vodPath(videoId)}/label`, {}, LABEL_TIMEOUT_MS);
}

export async function loadRecentVods(limit = 10): Promise<VodAnalysis[]> {
  return getJson<VodAnalysis[]>(`/api/vods?limit=${limit}`);
}
