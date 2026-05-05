import type { ChatMessage } from '../types';

export function compactChatMessage(message: ChatMessage): ChatMessage {
  return {
    message_id: message.message_id,
    channel_login: message.channel_login,
    channel_display_name: message.channel_display_name,
    session_id: message.session_id,
    chatter_login: message.chatter_login,
    chatter_display_name: message.chatter_display_name,
    message_text: message.message_text,
    badges: [],
    emotes: message.emotes.map((emote) => ({ text: emote.text })),
    event_ts: message.event_ts,
    received_at: message.received_at
  };
}

export function compactChatMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages.map(compactChatMessage);
}
