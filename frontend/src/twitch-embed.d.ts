// Ambient types for the Twitch interactive embed script
// (https://player.twitch.tv/js/embed/v1.js), which installs `window.Twitch`.

interface TwitchPlayerOptions {
  video?: string;
  channel?: string;
  collection?: string;
  // Must list the hostname of the embedding page, or Twitch refuses to render.
  parent: string[];
  width?: number | string;
  height?: number | string;
  autoplay?: boolean;
  muted?: boolean;
  time?: string;
}

interface TwitchPlayer {
  seek(timestamp: number): void;
  getCurrentTime(): number;
  getDuration?(): number;
  setVideo(videoId: string, timestamp: number): void;
  pause(): void;
  play(): void;
  addEventListener(event: string, callback: (...args: unknown[]) => void): void;
  destroy?(): void;
}

interface TwitchPlayerConstructor {
  new (element: HTMLElement | string, options: TwitchPlayerOptions): TwitchPlayer;
  READY?: string;
  PLAYING?: string;
}

interface Window {
  Twitch?: {
    Player: TwitchPlayerConstructor;
  };
}
