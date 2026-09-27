import type { FeatureFlags, SettingsResponse, SettingValue } from '../types';
import { getJson, putJson } from './client';

// Saving can restart Twitch ingestion (EventSub resolves the user id first).
const SAVE_TIMEOUT_MS = 30_000;

export async function loadFeatures(): Promise<FeatureFlags> {
  return getJson<FeatureFlags>('/api/features');
}

export async function loadSettings(): Promise<SettingsResponse> {
  return getJson<SettingsResponse>('/api/settings');
}

// A null value clears the saved override, falling back to the .env value.
export async function saveSettings(values: Record<string, SettingValue>): Promise<SettingsResponse> {
  return putJson<SettingsResponse>('/api/settings', { values }, SAVE_TIMEOUT_MS);
}
