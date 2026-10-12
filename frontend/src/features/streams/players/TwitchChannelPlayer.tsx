import React from 'react';

import { loadTwitchEmbed } from '../../../utils/twitchEmbed';

// If the player never reports READY (e.g. blocked autoplay quirks), stop
// showing the skeleton anyway so the iframe is visible.
const READY_FALLBACK_MS = 8000;

type TwitchChannelPlayerProps = {
  channel: string;
};

// Live Twitch channel embed (muted autoplay). Parents key this component on the
// channel, so the player is created once per mount and never re-targeted.
export function TwitchChannelPlayer({ channel }: TwitchChannelPlayerProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const playerRef = React.useRef<TwitchPlayer | null>(null);
  const channelRef = React.useRef(channel);
  const [state, setState] = React.useState<'loading' | 'ready' | 'failed'>('loading');

  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    let disposed = false;
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
          channel: channelRef.current,
          parent: [window.location.hostname],
          width: '100%',
          height: '100%',
          autoplay: true,
          muted: true
        });
        playerRef.current = player;

        const markReady = () => {
          if (!disposed) {
            setState('ready');
          }
        };
        player.addEventListener(PlayerCtor.READY ?? 'ready', markReady);
        fallbackTimer = window.setTimeout(markReady, READY_FALLBACK_MS);
      })
      .catch(() => {
        if (!disposed) {
          setState('failed');
        }
      });

    return () => {
      disposed = true;
      if (fallbackTimer !== null) {
        window.clearTimeout(fallbackTimer);
      }
      try {
        playerRef.current?.destroy?.();
      } catch {
        // Best effort; clearing the container below removes the iframe anyway.
      }
      playerRef.current = null;
      container.innerHTML = '';
    };
  }, []);

  // Should not happen (parents key on the channel), but keep the embed in sync if it does.
  React.useEffect(() => {
    if (channelRef.current === channel) {
      return;
    }
    channelRef.current = channel;
    playerRef.current?.setChannel?.(channel);
  }, [channel]);

  return (
    <div className="streams-player">
      {/* The Twitch script owns this node's children; React must not render into it. */}
      <div ref={containerRef} className="streams-player-frame" />
      {state === 'loading' ? <div className="streams-player-overlay skeleton" aria-label="Loading player" /> : null}
      {state === 'failed' ? (
        <div className="streams-player-overlay streams-player-failed">
          <p className="muted">
            The Twitch player could not be loaded. Open the channel on twitch.tv to watch.
          </p>
        </div>
      ) : null}
    </div>
  );
}
