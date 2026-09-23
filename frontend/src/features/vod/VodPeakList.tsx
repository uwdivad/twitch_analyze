import type { VodPeak } from '../../types';
import { formatOffset } from '../../utils/format';

type VodPeakListProps = {
  peaks: VodPeak[];
  currentTime: number;
  onSeek: (seconds: number) => void;
};

export function formatRate(messagesPerSecond: number): string {
  const value = Number.isFinite(messagesPerSecond) ? messagesPerSecond : 0;
  return `${value < 10 ? value.toFixed(1) : Math.round(value).toLocaleString()} msg/s`;
}

function keywordLine(peak: VodPeak): string {
  return [...peak.top_emotes.slice(0, 3), ...peak.top_tokens.slice(0, 3)].map((item) => item.value).join(' · ');
}

export function VodPeakList({ peaks, currentTime, onSeek }: VodPeakListProps) {
  const ordered = [...peaks].sort((a, b) => a.start_seconds - b.start_seconds);

  return (
    <ol className="vod-peak-list">
      {ordered.map((peak) => {
        const isActive = currentTime >= peak.start_seconds && currentTime < peak.end_seconds;
        const keywords = keywordLine(peak);
        return (
          <li key={peak.peak_id}>
            <button
              type="button"
              className={`vod-peak-row${isActive ? ' is-active' : ''}`}
              aria-current={isActive ? 'true' : undefined}
              onClick={() => onSeek(peak.start_seconds)}
              title={peak.sample_messages.length > 0 ? peak.sample_messages.slice(0, 3).join('\n') : undefined}
            >
              <span className="vod-peak-time vod-mono">{formatOffset(peak.start_seconds)}</span>
              <span className="vod-peak-body">
                <span className="vod-peak-title">{peak.title || peak.label || `Peak ${peak.peak_id}`}</span>
                {keywords ? <span className="vod-peak-keywords muted">{keywords}</span> : null}
              </span>
              <span className="vod-peak-rate vod-mono">{formatRate(peak.messages_per_second)}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
