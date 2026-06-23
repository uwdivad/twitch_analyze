import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from openai import OpenAI

from app.models.chat import ChatSummary, SummaryContext
from app.storage.clickhouse import ClickHouseRepository


class SummaryService:
    def __init__(
        self,
        *,
        clickhouse: ClickHouseRepository,
        api_key: str,
        model: str,
        max_messages: int,
    ) -> None:
        self._clickhouse = clickhouse
        self._api_key = api_key
        self._model = model
        self._max_messages = max_messages

    async def generate(self, channel: str, window_minutes: int) -> ChatSummary:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        context = await self._clickhouse.summary_context(
            channel=channel,
            window_minutes=window_minutes,
            max_messages=self._max_messages,
        )
        if context is None:
            raise ValueError("No chat messages found for that channel and window")

        summary_text = await asyncio.to_thread(self._call_openai, context)
        summary = ChatSummary(
            summary_id=str(uuid4()),
            channel_id=context.channel_id,
            channel_login=context.channel_login,
            session_id=context.session_id,
            window_start=context.window_start,
            window_end=context.window_end,
            window_size=context.window_size,
            summary_text=summary_text.strip(),
            model=self._model,
            source_stats=context.stats,
            created_at=datetime.now(UTC),
        )
        await self._clickhouse.insert_summary(summary)
        return summary

    def _call_openai(self, context: SummaryContext) -> str:
        client = OpenAI(api_key=self._api_key)
        response = client.responses.create(
            model=self._model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You summarize Twitch chat analytics for a dashboard. "
                        "Be concise, concrete, and avoid inventing facts that are not supported by the provided data."
                    ),
                },
                {
                    "role": "user",
                    "content": self._prompt(context),
                },
            ],
        )
        return response.output_text

    def _prompt(self, context: SummaryContext) -> str:
        stats = context.stats
        top_chatters = ", ".join(f"{item.value} ({item.count})" for item in stats.top_chatters) or "none"
        top_emotes = ", ".join(f"{item.value} ({item.count})" for item in stats.top_emotes) or "none"
        spike_windows = (
            "\n".join(
                f"- {window.bucket.isoformat()}: {window.message_count} messages, "
                f"{window.unique_chatter_count} unique chatters"
                for window in stats.spike_windows
            )
            or "- None detected."
        )
        messages = "\n".join(
            f"[{message.event_ts.isoformat()}] {message.chatter_login}: {message.message_text}"
            for message in context.sample_messages
        )
        return f"""
Summarize this Twitch chat window.

Channel: {context.channel_login}
Window start: {context.window_start.isoformat()}
Window end: {context.window_end.isoformat()}
Message count: {stats.message_count}
Unique chatters: {stats.unique_chatter_count}
Top chatters: {top_chatters}
Top emotes: {top_emotes}
Sampled messages: {stats.sampled_message_count}
Spike windows:
{spike_windows}

Return this exact Markdown structure:

### Short recap
2-4 sentences.

### Main topics
- 3-6 bullets.

### Common questions
- 0-5 bullets. Use "None obvious from the sample." if there are none.

### Mood
1 sentence describing the visible chat mood and why.

### Notable phrases or emotes
- 0-5 bullets.

### Activity spikes
- Include the spike timestamp in ISO format for each detected spike window. Briefly explain what chat seemed to discuss there if the sampled messages support it.

Messages:
{messages}
""".strip()
