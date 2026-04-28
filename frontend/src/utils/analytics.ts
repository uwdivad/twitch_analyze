import type { ChatMessage, TopItem, VolumePoint } from '../types';

export function minuteBucket(value: string): string {
  const date = new Date(value);
  date.setSeconds(0, 0);
  return date.toISOString();
}

export function incrementVolume(
  current: VolumePoint[],
  message: ChatMessage,
  minuteChatters: Map<string, Set<string>>
): VolumePoint[] {
  const bucket = minuteBucket(message.event_ts);
  const chatter = message.chatter_login || message.chatter_display_name || message.message_id;
  const chatters = minuteChatters.get(bucket) ?? new Set<string>();
  const wasNewChatter = !chatters.has(chatter);
  chatters.add(chatter);
  minuteChatters.set(bucket, chatters);

  const existing = current.find((point) => point.bucket === bucket);
  if (existing) {
    return current.map((point) =>
      point.bucket === bucket
        ? {
            ...point,
            message_count: point.message_count + 1,
            unique_chatter_count: point.unique_chatter_count + (wasNewChatter ? 1 : 0)
          }
        : point
    );
  }
  return [...current, { bucket, message_count: 1, unique_chatter_count: 1 }].slice(-120);
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

export function buildVolumeFromMessages(messages: ChatMessage[]): VolumePoint[] {
  const minuteChatters = new Map<string, Set<string>>();
  return messages.reduce<VolumePoint[]>(
    (current, message) => incrementVolume(current, message, minuteChatters),
    []
  );
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

export function mergeMessages(existing: ChatMessage[], incoming: ChatMessage[], limit = 150): ChatMessage[] {
  const byId = new Map<string, ChatMessage>();
  for (const message of [...existing, ...incoming]) {
    byId.set(message.message_id, message);
  }
  return [...byId.values()]
    .sort((a, b) => new Date(a.event_ts).getTime() - new Date(b.event_ts).getTime())
    .slice(-limit);
}

export function mergeVolumeSeries(...series: VolumePoint[][]): VolumePoint[] {
  const byBucket = new Map<string, VolumePoint>();

  for (const points of series) {
    for (const point of points) {
      const existing = byBucket.get(point.bucket);
      byBucket.set(
        point.bucket,
        existing
          ? {
              bucket: point.bucket,
              message_count: Math.max(existing.message_count, point.message_count),
              unique_chatter_count: Math.max(existing.unique_chatter_count, point.unique_chatter_count)
            }
          : point
      );
    }
  }

  return [...byBucket.values()]
    .sort((a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime())
    .slice(-120);
}
