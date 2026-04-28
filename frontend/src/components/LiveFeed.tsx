import type { ChatMessage } from '../types';
import { formatTime } from '../utils/format';

type LiveFeedProps = {
  activeChannel: string;
  messages: ChatMessage[];
};

export function LiveFeed({ activeChannel, messages }: LiveFeedProps) {
  return (
    <section className="panel feed-panel">
      <div className="panel-heading">
        <h2>Live Feed</h2>
        <span>{activeChannel || 'all channels'}</span>
      </div>
      <div className="feed">
        {messages.length === 0 ? (
          <div className="empty">No messages captured yet.</div>
        ) : (
          messages
            .slice()
            .reverse()
            .map((message) => (
              <article key={`${message.message_id}-${message.received_at}`} className="message-row">
                <time>{formatTime(message.event_ts)}</time>
                <strong>{message.chatter_display_name || message.chatter_login}</strong>
                <p>{message.message_text}</p>
              </article>
            ))
        )}
      </div>
    </section>
  );
}
