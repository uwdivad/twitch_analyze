import type {
  ChannelInfo,
  ChannelVolumeSeries,
  ChatMessage,
  ChatSummary,
  MessageTotal,
  TopItem,
  TranscriptionJob,
  VolumePoint
} from '../types';
import { RECENT_MESSAGE_LIMIT } from '../config';
import { compactChatMessages } from '../utils/messages';

export const API_BASE = import.meta.env.VITE_API_BASE ?? '';

// Every request aborts after this long so a single hung fetch can never wedge
// the dashboard reload loop indefinitely.
const REQUEST_TIMEOUT_MS = 15_000;

// Non-2xx response. `message` is FastAPI's `detail` when readable.
export class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

// The request was aborted by its timeout budget.
export class RequestTimeoutError extends Error {
  constructor() {
    super('Request timed out');
    this.name = 'RequestTimeoutError';
  }
}

// Builds an HttpError from a failed response, preferring FastAPI's `detail`
// (a string for HTTPException, or a list of {msg} objects for 422 validation).
export async function readError(response: Response): Promise<HttpError> {
  let message = `${response.status} ${response.statusText}`.trim();
  try {
    const payload: unknown = await response.json();
    const detail = (payload as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string' && detail) {
      message = detail;
    } else if (Array.isArray(detail)) {
      const msgs = detail
        .map((item) => (item && typeof item === 'object' ? (item as { msg?: unknown }).msg : null))
        .filter((msg): msg is string => typeof msg === 'string' && msg.length > 0);
      if (msgs.length > 0) {
        message = msgs.join('; ');
      }
    }
  } catch {
    // Keep the HTTP status fallback when the response is not JSON.
  }
  return new HttpError(response.status, message);
}

async function requestJson<T>(path: string, init: RequestInit, timeoutMs: number): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) {
      throw await readError(response);
    }
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && (err.name === 'TimeoutError' || err.name === 'AbortError')) {
      throw new RequestTimeoutError();
    }
    throw err;
  }
}

export async function getJson<T>(path: string): Promise<T> {
  return requestJson<T>(path, {}, REQUEST_TIMEOUT_MS);
}

export async function postJson<T>(path: string, body: unknown, timeoutMs = REQUEST_TIMEOUT_MS): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    },
    timeoutMs
  );
}

function channelQuery(channel: string): string {
  return channel ? `?channel=${encodeURIComponent(channel)}` : '';
}

function appendLimit(query: string, limit: number): string {
  return `${query}${query ? '&' : '?'}limit=${limit}`;
}

export async function loadDashboardData(channel: string, volumeWindowMinutes: number) {
  const query = channelQuery(channel);
  const [channels, messages, volume, messageTotal, topChatters, topEmotes, channelVolumes] = await Promise.all([
    getJson<ChannelInfo[]>('/api/channels'),
    getJson<ChatMessage[]>(`/api/messages/recent${appendLimit(query, RECENT_MESSAGE_LIMIT)}`),
    getJson<VolumePoint[]>(`/api/analytics/volume${appendLimit(query, volumeWindowMinutes)}`),
    getJson<MessageTotal>(`/api/analytics/message-total${query}`),
    getJson<TopItem[]>(`/api/analytics/top-chatters${appendLimit(query, 10)}`),
    getJson<TopItem[]>(`/api/analytics/top-emotes${appendLimit(query, 10)}`),
    channel
      ? Promise.resolve<ChannelVolumeSeries>({})
      : getJson<ChannelVolumeSeries>(`/api/analytics/volume-by-channel?limit=${volumeWindowMinutes}`)
  ]);

  return {
    channels,
    messages: compactChatMessages(messages),
    messageTotal: messageTotal.count,
    volume,
    channelVolumes,
    topChatters,
    topEmotes
  };
}

export async function loadSummaries(channel: string, limit = 10): Promise<ChatSummary[]> {
  const query = appendLimit(channelQuery(channel), limit);
  return getJson<ChatSummary[]>(`/api/summaries${query}`);
}

export async function generateSummary(channel: string, windowMinutes: number): Promise<ChatSummary> {
  return postJson<ChatSummary>('/api/summaries/generate', {
    channel,
    window_minutes: windowMinutes
  });
}

export async function startTranscription(channel: string, durationMinutes: number): Promise<TranscriptionJob> {
  return postJson<TranscriptionJob>('/api/transcriptions/start', {
    channel,
    duration_minutes: durationMinutes
  });
}

export async function loadTranscriptionJob(jobId: string): Promise<TranscriptionJob> {
  return getJson<TranscriptionJob>(`/api/transcriptions/jobs/${encodeURIComponent(jobId)}`);
}
