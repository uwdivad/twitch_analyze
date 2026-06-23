import React from 'react';

import type { ChatSummary } from '../types';

const SUMMARY_WINDOWS = [
  { label: '15m', minutes: 15 },
  { label: '1h', minutes: 60 },
  { label: '24h', minutes: 1440 }
];

type SummaryPanelProps = {
  activeChannel: string;
  summaries: ChatSummary[];
  isLoading: boolean;
  error: string;
  onGenerate: (windowMinutes: number) => void;
};

function formatSummaryWindow(summary: ChatSummary): string {
  const start = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(summary.window_start));
  const end = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(summary.window_end));
  return `${start} - ${end}`;
}

function formatSpikeTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
}

export function SummaryPanel({ activeChannel, summaries, isLoading, error, onGenerate }: SummaryPanelProps) {
  const [windowMinutes, setWindowMinutes] = React.useState(60);

  return (
    <section className="panel summary-panel">
      <div className="panel-heading summary-heading">
        <div>
          <h2>Chat Summary</h2>
          <span>{activeChannel ? `#${activeChannel}` : 'Select a channel to generate summaries'}</span>
        </div>
        <div className="summary-actions">
          <select
            aria-label="Summary window"
            value={windowMinutes}
            onChange={(event) => setWindowMinutes(Number(event.target.value))}
          >
            {SUMMARY_WINDOWS.map((option) => (
              <option key={option.minutes} value={option.minutes}>
                {option.label}
              </option>
            ))}
          </select>
          <button type="button" disabled={!activeChannel || isLoading} onClick={() => onGenerate(windowMinutes)}>
            {isLoading ? 'Generating' : 'Generate'}
          </button>
        </div>
      </div>

      {error ? <div className="summary-error">{error}</div> : null}

      <div className="summary-list">
        {summaries.length === 0 ? (
          <div className="empty">
            {activeChannel ? 'No summaries yet.' : 'Choose one channel from the controls above.'}
          </div>
        ) : (
          summaries.map((summary) => (
            <article key={summary.summary_id} className="summary-card">
              <header>
                <strong>{formatSummaryWindow(summary)}</strong>
                <span>
                  {summary.source_stats.message_count.toLocaleString()} messages by{' '}
                  {summary.source_stats.unique_chatter_count.toLocaleString()} chatters
                </span>
              </header>
              <div className="summary-meta">
                <span>{summary.window_size}</span>
                <span>{summary.model}</span>
              </div>
              {summary.source_stats.spike_windows.length > 0 ? (
                <div className="summary-spikes">
                  <strong>Spike windows</strong>
                  <span>
                    {summary.source_stats.spike_windows
                      .map((window) => `${formatSpikeTime(window.bucket)} (${window.message_count})`)
                      .join(', ')}
                  </span>
                </div>
              ) : null}
              <pre>{summary.summary_text}</pre>
            </article>
          ))
        )}
      </div>
    </section>
  );
}
