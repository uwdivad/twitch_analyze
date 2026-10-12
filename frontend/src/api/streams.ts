import type { StreamPlatform, StreamStatusResponse } from '../types';
import { getJson } from './client';

export type StreamHandleOverrides = Partial<Record<StreamPlatform, string>>;

// One name is checked on every platform; an override swaps the handle for one of them
// (e.g. a YouTube handle that differs from the Twitch login).
export async function lookupStreamStatus(name: string, overrides: StreamHandleOverrides = {}): Promise<StreamStatusResponse> {
  const params = new URLSearchParams({ name });
  for (const platform of ['twitch', 'kick', 'youtube'] as const) {
    const handle = overrides[platform]?.trim();
    if (handle) {
      params.set(platform, handle);
    }
  }
  return getJson<StreamStatusResponse>(`/api/streams/status?${params.toString()}`);
}
