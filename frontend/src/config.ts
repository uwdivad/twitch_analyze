export const RECENT_MESSAGE_LIMIT = 500;
export const LIVE_FEED_RENDER_LIMIT = 100;
export const MAX_LIVE_MESSAGES_PER_FLUSH = 1000;

export const LIVE_UPDATE_INTERVAL_OPTIONS = [
  { label: 'Live', milliseconds: 1000 },
  { label: 'Every 10s', milliseconds: 10_000 },
  { label: 'Every 30s', milliseconds: 30_000 },
  { label: 'Every 1m', milliseconds: 60_000 },
  { label: 'Every 5m', milliseconds: 300_000 }
] as const;

export const DEFAULT_LIVE_UPDATE_INTERVAL_MS = 10_000;

export const VOLUME_WINDOW_OPTIONS = [
  { label: '15m', minutes: 15 },
  { label: '1h', minutes: 60 },
  { label: '2h', minutes: 120 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 }
] as const;

export const DEFAULT_VOLUME_WINDOW_MINUTES = 120;
