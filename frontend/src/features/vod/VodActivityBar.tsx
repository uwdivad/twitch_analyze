import React from 'react';

import type { VodActivity, VodActivityBucket, VodPeak } from '../../types';
import { formatOffset } from '../../utils/format';

type VodActivityBarProps = {
  activity: VodActivity | null;
  peaks: VodPeak[];
  currentTime: number;
  durationSeconds: number;
  onSeek: (seconds: number) => void;
};

// Vertical lanes in SVG pixel space (fixed height; width follows the wrapper).
const HEIGHT = 120;
const LABEL_BASELINE = 11;
const MARKER_TOP = 15;
const MARKER_TIP = 24;
const BARS_TOP = 30;
const BARS_BOTTOM = 100;
const TICK_LABEL_BASELINE = 115;

const LABEL_MAX_CHARS = 18;
// Rough advance width of an 11px UI font; used only to avoid label collisions.
const LABEL_CHAR_PX = 6.2;
const LABEL_GAP_PX = 8;
const MIN_TICK_SPACING_PX = 72;
const TICK_STEPS_SECONDS = [300, 600, 900, 1800, 3600, 7200, 14400];
// Used for clamping until the tooltip has been measured once.
const TOOLTIP_FALLBACK_WIDTH = 240;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

function peakText(peak: VodPeak): string {
  return peak.title || peak.label || `Peak ${peak.peak_id}`;
}

// A bar with 2px-rounded top corners anchored to the baseline.
function barPath(x: number, y: number, w: number): string {
  const r = Math.min(2, w / 2, BARS_BOTTOM - y);
  if (r <= 0.25) {
    return `M${x},${BARS_BOTTOM}V${y}H${x + w}V${BARS_BOTTOM}Z`;
  }
  return (
    `M${x},${BARS_BOTTOM}V${y + r}Q${x},${y} ${x + r},${y}` +
    `H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${BARS_BOTTOM}Z`
  );
}

function useElementWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = React.useRef<T | null>(null);
  const [width, setWidth] = React.useState(0);

  React.useLayoutEffect(() => {
    const element = ref.current;
    if (!element) {
      return;
    }
    setWidth(Math.floor(element.getBoundingClientRect().width));
    if (typeof ResizeObserver === 'undefined') {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        setWidth(Math.floor(entry.contentRect.width));
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}

type Hover = { x: number; bucket: VodActivityBucket };

export function VodActivityBar({ activity, peaks, currentTime, durationSeconds, onSeek }: VodActivityBarProps) {
  const [wrapperRef, width] = useElementWidth<HTMLDivElement>();
  const [hover, setHover] = React.useState<Hover | null>(null);
  const tooltipRef = React.useRef<HTMLDivElement | null>(null);
  const [tooltipWidth, setTooltipWidth] = React.useState(TOOLTIP_FALLBACK_WIDTH);

  // Measure the tooltip after each hover change so it can be clamped inside the bar.
  React.useLayoutEffect(() => {
    const measured = tooltipRef.current?.offsetWidth;
    if (measured && measured !== tooltipWidth) {
      setTooltipWidth(measured);
    }
  }, [hover, tooltipWidth]);

  // The tooltip is centred on `left` (translateX(-50%)); keep it fully inside.
  const tooltipLeft = (x: number) => {
    const half = tooltipWidth / 2;
    return width <= tooltipWidth ? width / 2 : Math.min(Math.max(x, half), width - half);
  };

  const bucketSeconds = activity?.bucket_seconds ?? 0;
  const duration = React.useMemo(() => {
    const lastBucketEnd = activity && activity.buckets.length > 0
      ? activity.buckets[activity.buckets.length - 1].offset_seconds + activity.bucket_seconds
      : 0;
    return Math.max(durationSeconds || 0, activity?.duration_seconds || 0, lastBucketEnd, 1);
  }, [activity, durationSeconds]);

  const toX = React.useCallback((seconds: number) => (Math.min(Math.max(seconds, 0), duration) / duration) * width, [
    duration,
    width
  ]);

  const bucketByIndex = React.useMemo(() => {
    const map = new Map<number, VodActivityBucket>();
    activity?.buckets.forEach((bucket) => map.set(bucket.index, bucket));
    return map;
  }, [activity]);

  // Two compound paths (normal + inside-a-peak) instead of one element per
  // bucket: a long VOD at 5s buckets is thousands of bars.
  const bars = React.useMemo(() => {
    if (!activity || activity.buckets.length === 0 || width <= 0 || bucketSeconds <= 0) {
      return null;
    }
    const maxCount = activity.buckets.reduce((max, bucket) => Math.max(max, bucket.message_count), 1);
    const slotPx = (bucketSeconds / duration) * width;
    const bucketCount = Math.ceil(duration / bucketSeconds);
    const gap = bucketCount > width ? 0 : 1;
    const barWidth = Math.max(1, slotPx - gap);
    const extents = peaks.map((peak) => [peak.start_seconds, peak.end_seconds] as const);

    let normal = '';
    let highlighted = '';
    for (const bucket of activity.buckets) {
      if (bucket.message_count <= 0) {
        continue;
      }
      const x = toX(bucket.offset_seconds);
      const h = (bucket.message_count / maxCount) * (BARS_BOTTOM - BARS_TOP);
      const y = BARS_BOTTOM - Math.max(1, h);
      const w = Math.min(barWidth, Math.max(1, width - x));
      const d = barPath(x, y, w);
      const bucketEnd = bucket.offset_seconds + bucketSeconds;
      const inPeak = extents.some(([start, end]) => bucket.offset_seconds < end && bucketEnd > start);
      if (inPeak) {
        highlighted += d;
      } else {
        normal += d;
      }
    }
    return { normal, highlighted };
  }, [activity, bucketSeconds, duration, peaks, toX, width]);

  const ticks = React.useMemo(() => {
    if (width <= 0) {
      return [];
    }
    const maxTicks = Math.max(1, Math.floor(width / MIN_TICK_SPACING_PX));
    const step = TICK_STEPS_SECONDS.find((candidate) => duration / candidate <= maxTicks)
      ?? TICK_STEPS_SECONDS[TICK_STEPS_SECONDS.length - 1];
    const result: Array<{ seconds: number; x: number; anchor: 'start' | 'middle' | 'end' }> = [];
    for (let seconds = 0; seconds <= duration; seconds += step) {
      const x = toX(seconds);
      if (seconds > 0 && width - x < 24) {
        break;
      }
      result.push({ seconds, x, anchor: seconds === 0 ? 'start' : 'middle' });
    }
    return result;
  }, [duration, toX, width]);

  // Place labels greedily by score so the strongest peaks win collisions.
  const markers = React.useMemo(() => {
    const placed: Array<[number, number]> = [];
    const byScore = [...peaks].sort((a, b) => b.score - a.score);
    const labelled = new Map<number, { text: string; anchor: 'start' | 'end' }>();
    for (const peak of byScore) {
      const x = toX(peak.peak_seconds);
      const text = truncate(peakText(peak), LABEL_MAX_CHARS);
      const textWidth = text.length * LABEL_CHAR_PX;
      const anchor: 'start' | 'end' = x + textWidth + 4 > width ? 'end' : 'start';
      const left = anchor === 'start' ? x - 2 : x - textWidth - 4;
      const right = anchor === 'start' ? x + textWidth + 4 : x + 2;
      if (placed.some(([l, r]) => left < r + LABEL_GAP_PX && right + LABEL_GAP_PX > l)) {
        continue;
      }
      placed.push([left, right]);
      labelled.set(peak.peak_id, { text, anchor });
    }
    return peaks.map((peak) => ({ peak, x: toX(peak.peak_seconds), label: labelled.get(peak.peak_id) ?? null }));
  }, [peaks, toX, width]);

  const secondsAt = (clientX: number, svg: SVGSVGElement): { x: number; seconds: number } => {
    const rect = svg.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - rect.left, 0), width);
    return { x, seconds: width > 0 ? (x / width) * duration : 0 };
  };

  const handleMove = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!activity || bucketSeconds <= 0) {
      return;
    }
    const { seconds } = secondsAt(event.clientX, event.currentTarget);
    const index = Math.floor(seconds / bucketSeconds);
    const bucket = bucketByIndex.get(index) ?? {
      index,
      offset_seconds: index * bucketSeconds,
      message_count: 0,
      unique_chatter_count: 0
    };
    setHover({ x: toX(bucket.offset_seconds + bucketSeconds / 2), bucket });
  };

  const handleClick = (event: React.MouseEvent<SVGSVGElement>) => {
    onSeek(secondsAt(event.clientX, event.currentTarget).seconds);
  };

  const playheadX = toX(currentTime);
  const ariaLabel = `Chat activity across ${formatOffset(duration)} of VOD, ${peaks.length} ${
    peaks.length === 1 ? 'peak' : 'peaks'
  } marked. Click to seek.`;

  return (
    <div ref={wrapperRef} className="vod-activity">
      {width > 0 ? (
        <svg
          className="vod-activity-svg"
          width={width}
          height={HEIGHT}
          viewBox={`0 0 ${width} ${HEIGHT}`}
          role="group"
          aria-label={ariaLabel}
          onMouseMove={handleMove}
          onMouseLeave={() => setHover(null)}
          onClick={handleClick}
        >
          {/* Hit area so the whole strip is clickable, not only the bars. */}
          <rect x={0} y={0} width={width} height={HEIGHT} className="vod-activity-hit" />
          <line x1={0} x2={width} y1={BARS_BOTTOM + 0.5} y2={BARS_BOTTOM + 0.5} className="vod-activity-baseline" />

          {bars ? (
            <>
              <path d={bars.normal} className="vod-bar" />
              <path d={bars.highlighted} className="vod-bar is-peak" />
            </>
          ) : null}

          {hover ? (
            <rect
              x={Math.max(0, hover.x - Math.max(2, ((bucketSeconds / duration) * width) / 2))}
              y={BARS_TOP}
              width={Math.max(4, (bucketSeconds / duration) * width)}
              height={BARS_BOTTOM - BARS_TOP}
              className="vod-activity-hover"
            />
          ) : null}

          {ticks.map((tick) => (
            <g key={tick.seconds} className="vod-tick">
              <line x1={tick.x + 0.5} x2={tick.x + 0.5} y1={BARS_BOTTOM} y2={BARS_BOTTOM + 4} />
              <text x={tick.x} y={TICK_LABEL_BASELINE} textAnchor={tick.anchor}>
                {formatOffset(tick.seconds)}
              </text>
            </g>
          ))}

          {markers.map(({ peak, x, label }) => {
            const fullLabel = `${peakText(peak)} · ${formatOffset(peak.start_seconds)}`;
            const seek = () => onSeek(peak.start_seconds);
            return (
              <g
                key={peak.peak_id}
                className="vod-peak-marker"
                role="button"
                tabIndex={0}
                aria-label={`Seek to peak: ${fullLabel}`}
                onClick={(event) => {
                  event.stopPropagation();
                  seek();
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    seek();
                  }
                }}
                onMouseMove={(event) => event.stopPropagation()}
                onMouseEnter={() => setHover(null)}
              >
                <title>{fullLabel}</title>
                <rect x={x - 6} y={0} width={12} height={BARS_BOTTOM} className="vod-peak-hit" />
                <line x1={x} x2={x} y1={MARKER_TIP} y2={BARS_BOTTOM} className="vod-peak-line" />
                <path d={`M${x - 4},${MARKER_TOP}H${x + 4}L${x},${MARKER_TIP}Z`} className="vod-peak-flag" />
                {label ? (
                  <text
                    x={label.anchor === 'start' ? x - 2 : x + 2}
                    y={LABEL_BASELINE}
                    textAnchor={label.anchor}
                    className="vod-peak-text"
                  >
                    {label.text}
                  </text>
                ) : null}
              </g>
            );
          })}

          <line
            x1={playheadX}
            x2={playheadX}
            y1={MARKER_TOP}
            y2={BARS_BOTTOM + 4}
            className="vod-playhead"
            pointerEvents="none"
          />
        </svg>
      ) : null}

      {!activity ? <div className="vod-activity-skeleton skeleton" aria-hidden="true" /> : null}

      {hover ? (
        <div
          ref={tooltipRef}
          className="vod-activity-tooltip tooltip-card"
          style={{ left: tooltipLeft(hover.x) }}
          role="status"
        >
          <span className="vod-mono">{formatOffset(hover.bucket.offset_seconds)}</span>
          {' · '}
          {hover.bucket.message_count.toLocaleString()} msgs
          {' · '}
          {hover.bucket.unique_chatter_count.toLocaleString()} chatters
        </div>
      ) : null}
    </div>
  );
}
