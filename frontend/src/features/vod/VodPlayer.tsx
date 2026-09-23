import React from 'react';

const EMBED_SCRIPT_URL = 'https://player.twitch.tv/js/embed/v1.js';
const TIME_POLL_MS = 500;
// If the player never reports READY (e.g. blocked autoplay quirks), stop
// showing the skeleton anyway so the iframe is visible.
const READY_FALLBACK_MS = 8000;

let embedPromise: Promise<void> | null = null;

// Appends the Twitch embed script once per page and resolves when
// window.Twitch.Player is available. A failed load clears the cache so a later
// mount can retry.
function loadTwitchEmbed(): Promise<void> {
  if (window.Twitch?.Player) {
    return Promise.resolve();
  }
  if (embedPromise) {
    return embedPromise;
  }
  embedPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = EMBED_SCRIPT_URL;
    script.async = true;
    script.onload = () => {
      if (window.Twitch?.Player) {
        resolve();
      } else {
        reject(new Error('Twitch embed script loaded without window.Twitch.Player'));
      }
    };
    script.onerror = () => {
      script.remove();
      reject(new Error('Failed to load the Twitch embed script'));
    };
    document.head.appendChild(script);
  }).catch((err: unknown) => {
    embedPromise = null;
    throw err;
  });
  return embedPromise;
}

export type VodPlayerHandle = {
  // Returns false (and does nothing) until the player has reported READY.
  seek: (seconds: number) => boolean;
  getCurrentTime: () => number;
};

type VodPlayerProps = {
  videoId: string;
  onTimeUpdate: (seconds: number) => void;
};

export const VodPlayer = React.forwardRef<VodPlayerHandle, VodPlayerProps>(function VodPlayer(
  { videoId, onTimeUpdate },
  ref
) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const playerRef = React.useRef<TwitchPlayer | null>(null);
  const loadedVideoRef = React.useRef(videoId);
  const onTimeUpdateRef = React.useRef(onTimeUpdate);
  const readyRef = React.useRef(false);
  // Last time reported to onTimeUpdate; reset to force the next poll to re-sync.
  const lastTimeRef = React.useRef(-1);
  const [state, setState] = React.useState<'loading' | 'ready' | 'failed'>('loading');

  React.useEffect(() => {
    onTimeUpdateRef.current = onTimeUpdate;
  }, [onTimeUpdate]);

  React.useImperativeHandle(
    ref,
    () => ({
      seek: (seconds: number) => {
        const player = playerRef.current;
        if (!player || !readyRef.current) {
          return false;
        }
        player.seek(Math.max(0, seconds));
        lastTimeRef.current = -1;
        return true;
      },
      getCurrentTime: () => playerRef.current?.getCurrentTime() ?? 0
    }),
    []
  );

  // Create the player once per mount; video changes go through setVideo below.
  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let disposed = false;
    let pollTimer: number | null = null;
    let fallbackTimer: number | null = null;

    loadTwitchEmbed()
      .then(() => {
        if (disposed || !window.Twitch?.Player) {
          return;
        }
        const PlayerCtor = window.Twitch.Player;
        // `parent` must equal the hostname serving this page ("localhost" in
        // dev). Twitch refuses to embed on raw-IP hosts (e.g. 127.0.0.1 or a LAN
        // IP), so open the dashboard via a hostname for the player to work.
        const player = new PlayerCtor(container, {
          video: loadedVideoRef.current,
          parent: [window.location.hostname],
          width: '100%',
          height: '100%',
          autoplay: false
        });
        playerRef.current = player;

        const markReady = () => {
          readyRef.current = true;
          if (!disposed) {
            setState('ready');
          }
        };
        player.addEventListener(PlayerCtor.READY ?? 'ready', markReady);
        fallbackTimer = window.setTimeout(markReady, READY_FALLBACK_MS);

        pollTimer = window.setInterval(() => {
          let seconds: number;
          try {
            seconds = player.getCurrentTime();
          } catch {
            return;
          }
          if (Number.isFinite(seconds) && seconds !== lastTimeRef.current) {
            lastTimeRef.current = seconds;
            onTimeUpdateRef.current(seconds);
          }
        }, TIME_POLL_MS);
      })
      .catch(() => {
        if (!disposed) {
          setState('failed');
        }
      });

    return () => {
      disposed = true;
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
      }
      if (fallbackTimer !== null) {
        window.clearTimeout(fallbackTimer);
      }
      try {
        playerRef.current?.destroy?.();
      } catch {
        // Best effort; clearing the container below removes the iframe anyway.
      }
      playerRef.current = null;
      readyRef.current = false;
      lastTimeRef.current = -1;
      container.innerHTML = '';
    };
  }, []);

  React.useEffect(() => {
    if (loadedVideoRef.current === videoId) {
      return;
    }
    loadedVideoRef.current = videoId;
    playerRef.current?.setVideo(videoId, 0);
    onTimeUpdateRef.current(0);
  }, [videoId]);

  return (
    <div className="vod-player">
      {/* The Twitch script owns this node's children; React must not render into it. */}
      <div ref={containerRef} className="vod-player-frame" />
      {state === 'loading' ? <div className="vod-player-overlay skeleton" aria-label="Loading player" /> : null}
      {state === 'failed' ? (
        <div className="vod-player-overlay vod-player-failed">
          <p className="muted">
            The Twitch player could not be loaded. The activity bar and peak list still work; open the VOD on
            twitch.tv to watch.
          </p>
        </div>
      ) : null}
    </div>
  );
});
