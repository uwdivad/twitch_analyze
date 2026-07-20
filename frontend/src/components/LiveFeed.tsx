import React from 'react';
import { ArrowUp } from 'lucide-react';

import { KNOWN_BOTS, LIVE_FEED_RENDER_LIMIT } from '../config';
import type { ChatMessage } from '../types';
import { formatTime } from '../utils/format';

type LiveFeedProps = {
  activeChannel: string;
  messages: ChatMessage[];
  hideBots: boolean;
  onHideBotsChange: (hide: boolean) => void;
};

// Scrolling below this offset means the reader is looking at history, so stop
// shifting rows underneath them and batch new arrivals behind a resume pill.
const PAUSE_SCROLL_THRESHOLD_PX = 40;

export function LiveFeed({ activeChannel, messages, hideBots, onHideBotsChange }: LiveFeedProps) {
  const feedRef = React.useRef<HTMLDivElement>(null);
  const frozenRef = React.useRef<ChatMessage[]>([]);
  const [isPaused, setIsPaused] = React.useState(false);

  const visibleMessages = React.useMemo(() => {
    const filtered = hideBots
      ? messages.filter((message) => !KNOWN_BOTS.has(message.chatter_login.toLowerCase()))
      : messages;
    return filtered.slice(-LIVE_FEED_RENDER_LIMIT).reverse();
  }, [messages, hideBots]);

  if (!isPaused) {
    frozenRef.current = visibleMessages;
  }
  const displayedMessages = isPaused ? frozenRef.current : visibleMessages;

  let newCount = 0;
  if (isPaused) {
    const newestFrozenId = frozenRef.current[0]?.message_id;
    const index = newestFrozenId
      ? visibleMessages.findIndex((message) => message.message_id === newestFrozenId)
      : -1;
    newCount = index === -1 ? visibleMessages.length : index;
  }

  const handleScroll = React.useCallback((event: React.UIEvent<HTMLDivElement>) => {
    setIsPaused(event.currentTarget.scrollTop > PAUSE_SCROLL_THRESHOLD_PX);
  }, []);

  const handleResume = React.useCallback(() => {
    feedRef.current?.scrollTo({ top: 0 });
    setIsPaused(false);
  }, []);

  return (
    <section className="panel feed-panel">
      <div className="panel-heading">
        <h2>Live Feed</h2>
        <div className="feed-heading-tools">
          <label className="feed-filter">
            <input
              checked={hideBots}
              onChange={(event) => onHideBotsChange(event.target.checked)}
              type="checkbox"
            />
            Hide bots
          </label>
          <span>
            {activeChannel ? `#${activeChannel}` : 'all channels'} · latest {displayedMessages.length}
          </span>
        </div>
      </div>
      <div className="feed-wrap">
        {isPaused && newCount > 0 ? (
          <button className="feed-resume" onClick={handleResume} type="button">
            <ArrowUp size={14} />
            {newCount >= LIVE_FEED_RENDER_LIMIT ? `${LIVE_FEED_RENDER_LIMIT}+` : newCount} new
          </button>
        ) : null}
        <div className="feed" ref={feedRef} onScroll={handleScroll}>
          {displayedMessages.length === 0 ? (
            <div className="empty">
              {messages.length === 0 ? 'No messages captured yet.' : 'All recent messages are from bots.'}
            </div>
          ) : (
            displayedMessages.map((message) => (
              <article
                key={`${message.message_id}-${message.received_at}`}
                className={`message-row${activeChannel ? ' single-channel' : ''}`}
              >
                <time>{formatTime(message.event_ts)}</time>
                {activeChannel ? null : (
                  <span className="message-channel">{message.channel_display_name || message.channel_login}</span>
                )}
                <strong>{message.chatter_display_name || message.chatter_login}</strong>
                <p>{message.message_text}</p>
              </article>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
