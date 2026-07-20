export const RECENT_MESSAGE_LIMIT = 500;

// Common chat/service bots, matched against lowercase chatter_login.
export const KNOWN_BOTS = new Set([
  'streamelements',
  'nightbot',
  'fossabot',
  'moobot',
  'streamlabs',
  'soundalerts',
  'sery_bot',
  'wizebot',
  'botrix',
  'pokemoncommunitygame',
  'own3d',
  'creatisbot',
  'blerp',
  'lumiastream'
]);
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

// The live feed flushes at `liveUpdateIntervalMs`, but a full dashboard reload is a much
// heavier operation (several network requests + a full state replace). Floor its cadence
// so picking a fast "Live" display interval doesn't also flood the backend with reloads.
export const MIN_DASHBOARD_RELOAD_INTERVAL_MS = 10_000;

export const VOLUME_WINDOW_OPTIONS = [
  { label: '15m', minutes: 15 },
  { label: '1h', minutes: 60 },
  { label: '2h', minutes: 120 },
  { label: '6h', minutes: 360 },
  { label: '24h', minutes: 1440 }
] as const;

export const DEFAULT_VOLUME_WINDOW_MINUTES = 120;
