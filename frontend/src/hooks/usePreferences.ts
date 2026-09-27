import React from 'react';

import {
  DEFAULT_LIVE_UPDATE_INTERVAL_MS,
  DEFAULT_VOLUME_WINDOW_MINUTES,
  LIVE_UPDATE_INTERVAL_OPTIONS,
  VOLUME_WINDOW_OPTIONS
} from '../config';

// Per-browser dashboard preferences. The toolbar and the Settings page edit the
// same values, and they persist across reloads.
export type Preferences = {
  volumeWindowMinutes: number;
  liveUpdateIntervalMs: number;
  showLiveFeed: boolean;
  hideBots: boolean;
};

export const PREFERENCES_STORAGE_KEY = 'twitch-analyze-preferences';

export const DEFAULT_PREFERENCES: Preferences = {
  volumeWindowMinutes: DEFAULT_VOLUME_WINDOW_MINUTES,
  liveUpdateIntervalMs: DEFAULT_LIVE_UPDATE_INTERVAL_MS,
  showLiveFeed: true,
  hideBots: false
};

function readStoredPreferences(): Preferences {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(PREFERENCES_STORAGE_KEY) ?? 'null');
    if (!stored || typeof stored !== 'object') {
      return DEFAULT_PREFERENCES;
    }
    const value = stored as Partial<Record<keyof Preferences, unknown>>;
    // Drop anything no longer offered so a stale value can't select a missing option.
    return {
      volumeWindowMinutes: VOLUME_WINDOW_OPTIONS.some((option) => option.minutes === value.volumeWindowMinutes)
        ? (value.volumeWindowMinutes as number)
        : DEFAULT_PREFERENCES.volumeWindowMinutes,
      liveUpdateIntervalMs: LIVE_UPDATE_INTERVAL_OPTIONS.some(
        (option) => option.milliseconds === value.liveUpdateIntervalMs
      )
        ? (value.liveUpdateIntervalMs as number)
        : DEFAULT_PREFERENCES.liveUpdateIntervalMs,
      showLiveFeed: typeof value.showLiveFeed === 'boolean' ? value.showLiveFeed : DEFAULT_PREFERENCES.showLiveFeed,
      hideBots: typeof value.hideBots === 'boolean' ? value.hideBots : DEFAULT_PREFERENCES.hideBots
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export type SetPreference = <K extends keyof Preferences>(key: K, value: Preferences[K]) => void;

export function usePreferences(): [Preferences, SetPreference, () => void] {
  const [preferences, setPreferences] = React.useState<Preferences>(readStoredPreferences);

  React.useEffect(() => {
    try {
      window.localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      // Storage unavailable: preferences still apply for this session.
    }
  }, [preferences]);

  const setPreference = React.useCallback<SetPreference>((key, value) => {
    setPreferences((current) => (current[key] === value ? current : { ...current, [key]: value }));
  }, []);

  const resetPreferences = React.useCallback(() => setPreferences(DEFAULT_PREFERENCES), []);

  return [preferences, setPreference, resetPreferences];
}
