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
