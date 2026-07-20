import type { ChatMessage, TopItem, VolumePoint } from '../types';
import { DEFAULT_VOLUME_WINDOW_MINUTES, RECENT_MESSAGE_LIMIT } from '../config';

// Canonical minute-bucket key. The backend serializes VolumePoint.bucket without
// milliseconds ("...T12:34:00Z") while Date.toISOString() emits them
// ("...T12:34:00.000Z"), and all merging below is exact-string comparison — so
// EVERY bucket string, whether generated locally from a message timestamp or
// received from the server, must pass through this helper before it is compared
// or used as a map key.
export function minuteBucket(value: string): string {
  const date = new Date(value);
  date.setUTCSeconds(0, 0);
  return date.toISOString();
}

// Batch accumulation: one Map pass over the messages, O(messages + buckets),
// instead of a per-message find + full-array copy.
export function buildVolumeFromMessages(
  messages: ChatMessage[],
  limit = DEFAULT_VOLUME_WINDOW_MINUTES
): VolumePoint[] {
  const byBucket = new Map<string, { messageCount: number; chatters: Set<string> }>();
  for (const message of messages) {
    const bucket = minuteBucket(message.event_ts);
    let entry = byBucket.get(bucket);
    if (!entry) {
      entry = { messageCount: 0, chatters: new Set<string>() };
      byBucket.set(bucket, entry);
    }
    entry.messageCount += 1;
    entry.chatters.add(message.chatter_login || message.chatter_display_name || message.message_id);
  }

  return [...byBucket.entries()]
    .map(([bucket, entry]) => ({
      bucket,
      message_count: entry.messageCount,
      unique_chatter_count: entry.chatters.size
    }))
    .sort((a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime())
    .slice(-limit);
}

export function incrementTopItems(current: TopItem[], value: string): TopItem[] {
  if (!value) {
    return current;
  }
  const existing = current.find((item) => item.value === value);
  const next = existing
    ? current.map((item) => (item.value === value ? { ...item, count: item.count + 1 } : item))
    : [...current, { value, count: 1 }];
  return next.sort((a, b) => b.count - a.count).slice(0, 10);
}

export function incrementTopItemsByCounts(current: TopItem[], counts: Map<string, number>): TopItem[] {
  if (counts.size === 0) {
    return current;
  }

  const next = new Map<string, number>();
  for (const item of current) {
    next.set(item.value, item.count);
  }
  for (const [value, count] of counts) {
    if (value) {
      next.set(value, (next.get(value) ?? 0) + count);
    }
  }

  return [...next.entries()]
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);
}

export function buildTopChattersFromMessages(messages: ChatMessage[]): TopItem[] {
  return messages.reduce<TopItem[]>(
    (current, message) => incrementTopItems(current, message.chatter_login || message.chatter_display_name),
    []
  );
}

export function buildTopEmotesFromMessages(messages: ChatMessage[]): TopItem[] {
  return messages.reduce<TopItem[]>((current, message) => {
    let next = current;
    for (const emote of message.emotes) {
      const label = String(emote.text ?? '');
      if (label) {
        next = incrementTopItems(next, label);
      }
    }
    return next;
  }, []);
}

export function mergeMessages(existing: ChatMessage[], incoming: ChatMessage[], limit = RECENT_MESSAGE_LIMIT): ChatMessage[] {
  const byId = new Map<string, ChatMessage>();
  for (const message of [...existing, ...incoming]) {
    byId.set(message.message_id, message);
  }
  return [...byId.values()]
    .sort((a, b) => new Date(a.event_ts).getTime() - new Date(b.event_ts).getTime())
    .slice(-limit);
}

export function mergeVolumeSeries(series: VolumePoint[][], limit = DEFAULT_VOLUME_WINDOW_MINUTES): VolumePoint[] {
  const byBucket = new Map<string, VolumePoint>();

  for (const points of series) {
    for (const point of points) {
      // Normalize server-provided bucket strings so DB and locally-built points
      // for the same minute land on the same key.
      const bucket = minuteBucket(point.bucket);
      const existing = byBucket.get(bucket);
      // Overlapping buckets take the per-bucket max. This is a deliberate
      // approximation for boundary minutes where the DB series and the live
      // in-memory series each saw part (or all) of the same minute: summing
      // would double-count messages present in both series, so we accept
      // undercounting the boundary minute instead.
      byBucket.set(
        bucket,
        existing
          ? {
              bucket,
              message_count: Math.max(existing.message_count, point.message_count),
              unique_chatter_count: Math.max(existing.unique_chatter_count, point.unique_chatter_count)
            }
          : { ...point, bucket }
      );
    }
  }

  return [...byBucket.values()]
    .sort((a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime())
    .slice(-limit);
}
