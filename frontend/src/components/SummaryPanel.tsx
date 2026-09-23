import React from 'react';
import { ChevronDown, Sparkles } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

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
  const [collapsed, setCollapsed] = React.useState(true);

  React.useEffect(() => {
    if (isLoading || summaries.length > 0 || error) {
      setCollapsed(false);
    }
  }, [isLoading, summaries.length, error]);

  const headingHint = activeChannel
    ? `#${activeChannel}${summaries.length > 0 ? ` · ${summaries.length} summar${summaries.length === 1 ? 'y' : 'ies'}` : ''}`
    : 'Select a channel to generate summaries';

  return (
    <section className="card summary-panel">
      <div className="card-heading summary-heading">
        <div className="card-title">
          <h2>Chat summary</h2>
          <span className="card-meta">{headingHint}</span>
        </div>
        <div className="card-actions summary-actions">
          {activeChannel ? (
            <>
              <select
                aria-label="Summary window"
                className="field"
                value={windowMinutes}
                onChange={(event) => setWindowMinutes(Number(event.target.value))}
              >
                {SUMMARY_WINDOWS.map((option) => (
                  <option key={option.minutes} value={option.minutes}>
                    {option.label}
                  </option>
                ))}
              </select>
              <button
                className={`btn btn-accent${isLoading ? ' is-busy' : ''}`}
                type="button"
                disabled={isLoading}
                onClick={() => onGenerate(windowMinutes)}
              >
                <Sparkles size={16} />
                {isLoading ? 'Generating' : 'Generate'}
              </button>
            </>
          ) : null}
          <button
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand chat summaries' : 'Collapse chat summaries'}
            className={`btn btn-ghost btn-icon collapse-toggle${collapsed ? ' is-collapsed' : ''}`}
            onClick={() => setCollapsed((current) => !current)}
            type="button"
          >
            <ChevronDown size={16} />
          </button>
        </div>
      </div>

      {collapsed ? null : (
        <>
          {error ? <div className="alert summary-error">{error}</div> : null}

          <div className="summary-list">
            {isLoading ? (
              <article className="card summary-card summary-skeleton" aria-hidden="true">
                <div className="skeleton" style={{ width: '40%' }} />
                <div className="skeleton" style={{ width: '95%' }} />
                <div className="skeleton" style={{ width: '88%' }} />
                <div className="skeleton" style={{ width: '62%' }} />
              </article>
            ) : null}
            {summaries.length === 0 && !isLoading ? (
              <div className="empty">
                {activeChannel ? 'No summaries yet.' : 'Choose one channel from the controls above.'}
              </div>
            ) : (
              summaries.map((summary) => (
                <article key={summary.summary_id} className="card summary-card">
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
                  <div className="summary-markdown">
                    <ReactMarkdown>{summary.summary_text}</ReactMarkdown>
                  </div>
                </article>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}
