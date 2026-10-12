import type { ClipJob, ClipPlatform } from '../types';
import { API_BASE, getJson, postJson, readError } from './client';

function clipPath(jobId: string): string {
  return `/api/clips/${encodeURIComponent(jobId)}`;
}

// Note: X-API-Key is not sent by the client; this matches the other mutating
// calls. `require_api_key` is a no-op unless API_AUTH_TOKEN is set.
export async function createClip(channel: string, durationMinutes: number, platform: ClipPlatform = 'twitch'): Promise<ClipJob> {
  return postJson<ClipJob>('/api/clips', { channel, platform, duration_minutes: durationMinutes });
}

export async function loadClips(limit = 50): Promise<ClipJob[]> {
  return getJson<ClipJob[]>(`/api/clips?limit=${limit}`);
}

export async function loadClip(jobId: string): Promise<ClipJob> {
  return getJson<ClipJob>(clipPath(jobId));
}

// Plain link target: the browser downloads the MP4 directly.
export function clipDownloadUrl(jobId: string): string {
  return `${API_BASE}${clipPath(jobId)}/download`;
}

export async function deleteClip(jobId: string): Promise<void> {
  const response = await fetch(`${API_BASE}${clipPath(jobId)}`, { method: 'DELETE', signal: AbortSignal.timeout(15_000) });
  if (!response.ok) {
    throw await readError(response);
  }
}
