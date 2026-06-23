# LLM Chat Summary Flow

This document explains how manual Twitch chat summaries move through the app.

## User Flow

1. The dashboard loads channels, analytics, recent messages, and recent summaries.
2. The user selects one Twitch channel from the channel controls.
3. The `Chat Summary` panel becomes available for that selected channel.
4. The user chooses a summary window:
   - `15m`
   - `1h`
   - `24h`
5. The user clicks `Generate`.
6. The frontend sends a summary request to the backend.
7. The backend builds compact summary context from ClickHouse.
8. The backend sends that compact context to OpenAI.
9. The generated Markdown summary is saved in ClickHouse.
10. The frontend prepends the new summary to the dashboard list.

The first version is manual-only. There is no scheduled job yet.

## API Flow

Frontend call:

```text
POST /api/summaries/generate
```

Request body:

```json
{
  "channel": "example_channel",
  "window_minutes": 60
}
```

Backend route:

```text
backend/app/api/routes.py
```

The route calls:

```text
SummaryService.generate(channel, window_minutes)
```

The summary list uses:

```text
GET /api/summaries?channel=example_channel&limit=10
```

## Backend Summary Context

The backend does not send every historical chat message to the LLM.

Instead, `ClickHouseRepository.summary_context()` reads a bounded context from `chat_messages`:

- total message count
- unique chatter count
- top chatters
- top emotes
- sampled recent messages
- spike windows

The time filter is based on the selected window:

```text
event_ts >= window_start
event_ts < window_end
```

For a 1-hour summary, `window_start` is roughly:

```text
now - 60 minutes
```

and `window_end` is:

```text
now
```

## Spike Timestamp Flow

Spike windows are detected from minute-level message volume.

The repository groups chat messages by minute:

```sql
SELECT
    toStartOfMinute(event_ts) AS bucket,
    count() AS message_count,
    uniqExact(chatter_user_id) AS unique_chatter_count
FROM chat_messages
WHERE channel_login = ...
  AND event_ts >= window_start
  AND event_ts < window_end
GROUP BY bucket
HAVING message_count > 1
ORDER BY message_count DESC, bucket ASC
LIMIT 3
```

Each returned `bucket` is the UTC timestamp for the start of a high-activity minute.

Example:

```text
2026-05-07T01:42:00+00:00: 84 messages, 37 unique chatters
```

Those spike timestamps are stored in the summary source stats as:

```json
{
  "spike_windows": [
    {
      "bucket": "2026-05-07T01:42:00+00:00",
      "message_count": 84,
      "unique_chatter_count": 37
    }
  ]
}
```

The prompt explicitly tells the LLM to include an `Activity spikes` section and include these ISO timestamps. The dashboard also displays the spike windows outside the generated text, so the timestamps are visible even if the LLM summarizes them briefly.

## OpenAI Prompt Flow

`SummaryService` builds a prompt with:

- channel
- window start and end timestamps
- message count
- unique chatter count
- top chatters
- top emotes
- sampled message count
- spike window timestamps
- sampled messages with message timestamps

Sample message lines look like:

```text
[2026-05-07T01:43:15.123000+00:00] chatter_login: message text
```

The requested output structure is:

```text
### Short recap
### Main topics
### Common questions
### Mood
### Notable phrases or emotes
### Activity spikes
```

The `Activity spikes` section must include the spike timestamp in ISO format for each detected spike window.

## Storage Flow

Generated summaries are saved to the existing ClickHouse table:

```text
chat_summaries
```

The saved row includes:

- `summary_id`
- `channel_id`
- `channel_login`
- `session_id`
- `window_start`
- `window_size`
- `summary_text`
- `model`
- `source_stats`
- `created_at`

`source_stats` is JSON. It includes the computed stats, spike windows, and `window_end`.

## Frontend Display Flow

The dashboard component loads summaries when a channel is selected.

Relevant frontend files:

- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/components/SummaryPanel.tsx`
- `frontend/src/types.ts`

The `SummaryPanel` shows:

- selected channel
- summary window selector
- generate button
- message count
- unique chatter count
- model
- spike windows with timestamps
- generated Markdown text

If no channel is selected, generation is disabled.

If `OPENAI_API_KEY` is missing, the backend returns a clear configuration error and the rest of the dashboard continues working.

## Configuration

Local `.env` values:

```env
OPENAI_API_KEY=
OPENAI_SUMMARY_MODEL=gpt-5.2
OPENAI_SUMMARY_MAX_MESSAGES=250
```

GCP Terraform passes the same values into the VM startup-generated `.env`.

`OPENAI_SUMMARY_MAX_MESSAGES` controls how many sampled messages are included in the prompt. This keeps LLM cost bounded.
