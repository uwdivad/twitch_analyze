import React from 'react';

import { LIVE_FEED_RENDER_LIMIT } from '../config';
import type { ChatMessage } from '../types';
import { formatTime } from '../utils/format';

type LiveFeedProps = {
  activeChannel: string;
  messages: ChatMessage[];
};

export function LiveFeed({ activeChannel, messages }: LiveFeedProps) {
  const visibleMessages = React.useMemo(
    () => messages.slice(-LIVE_FEED_RENDER_LIMIT).reverse(),
    [messages]
  );

  return (
    <section className="panel feed-panel">
      <div className="panel-heading">
        <h2>Live Feed</h2>
        <span>
          {activeChannel || 'all channels'} · latest {visibleMessages.length}
          {messages.length > visibleMessages.length ? ` of ${messages.length}` : ''}
        </span>
      </div>
      <div className="feed">
        {messages.length === 0 ? (
          <div className="empty">No messages captured yet.</div>
        ) : (
          visibleMessages.map((message) => (
            <article key={`${message.message_id}-${message.received_at}`} className="message-row">
              <time>{formatTime(message.event_ts)}</time>
              <span className="message-channel">{message.channel_display_name || message.channel_login}</span>
              <strong>{message.chatter_display_name || message.chatter_login}</strong>
              <p>{message.message_text}</p>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
