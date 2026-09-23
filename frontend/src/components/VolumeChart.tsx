import React from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { useThemeColors } from '../hooks/useThemeColors';
import type { VolumePoint } from '../types';
import { formatMinuteTime } from '../utils/format';
import { ChartTooltip } from './ui/ChartTooltip';
import { SeriesLegend } from './ui/SeriesLegend';

type VolumeChartProps = {
  volume: VolumePoint[];
  windowLabel: string;
  windowMinutes: number;
};

const MINUTE_MS = 60_000;

// ClickHouse only returns non-empty minute buckets, so consecutive points can be
// hours apart. Rendering them side by side on a category axis hides the gap and
// distorts the timeline, so fill quiet minutes with zeros and clip to the window.
function fillAndClip(volume: VolumePoint[], windowMinutes: number): VolumePoint[] {
  if (volume.length === 0) {
    return [];
  }

  const sorted = [...volume].sort(
    (a, b) => new Date(a.bucket).getTime() - new Date(b.bucket).getTime()
  );
  const latest = new Date(sorted[sorted.length - 1].bucket).getTime();
  const cutoff = latest - windowMinutes * MINUTE_MS;
  const inWindow = sorted.filter((point) => new Date(point.bucket).getTime() > cutoff);

  const filled: VolumePoint[] = [];
  for (const point of inWindow) {
    const previous = filled[filled.length - 1];
    if (previous) {
      const target = new Date(point.bucket).getTime();
      let tick = new Date(previous.bucket).getTime() + MINUTE_MS;
      while (tick < target) {
        filled.push({
          bucket: new Date(tick).toISOString(),
          message_count: 0,
          unique_chatter_count: 0
        });
        tick += MINUTE_MS;
      }
    }
    filled.push(point);
  }
  return filled;
}

export function VolumeChart({ volume, windowLabel, windowMinutes }: VolumeChartProps) {
  const colors = useThemeColors();
  const summaryId = React.useId();
  const chartData = React.useMemo(
    () =>
      fillAndClip(volume, windowMinutes).map((point) => ({
        ...point,
        label: formatMinuteTime(point.bucket)
      })),
    [volume, windowMinutes]
  );

  const chartSummary = React.useMemo(() => {
    if (chartData.length === 0) {
      return '';
    }
    const peak = chartData.reduce((max, point) => Math.max(max, point.message_count), 0);
    const latest = chartData[chartData.length - 1];
    return `Line chart of messages and unique chatters per minute, last ${windowLabel}. Latest minute ${latest.message_count} messages from ${latest.unique_chatter_count} chatters; peak ${peak} messages per minute.`;
  }, [chartData, windowLabel]);

  const tick = { fill: colors.fgDim, fontSize: 12 };

  return (
    <div className="card chart-panel">
      <div className="card-heading">
        <div className="card-title">
          <h2>Messages per minute</h2>
          <span className="card-meta">last {windowLabel}</span>
        </div>
        <SeriesLegend
          items={[
            { label: 'Messages', color: colors.chart1 },
            { label: 'Unique chatters', color: colors.chart2 }
          ]}
        />
      </div>
      {volume.length === 0 ? (
        <div className="chart-empty">No analytics data yet. Live messages will update this chart immediately.</div>
      ) : (
        <div className="chart-frame" aria-describedby={summaryId}>
          <p className="sr-only" id={summaryId}>
            {chartSummary}
          </p>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke={colors.border} strokeWidth={1} vertical={false} />
              <XAxis
                dataKey="label"
                tick={tick}
                tickLine={false}
                axisLine={{ stroke: colors.border }}
                minTickGap={32}
                tickMargin={8}
              />
              <YAxis tick={tick} tickLine={false} axisLine={false} allowDecimals={false} width={48} />
              <Tooltip
                content={<ChartTooltip />}
                cursor={{ stroke: colors.borderStrong, strokeWidth: 1 }}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="message_count"
                name="Messages"
                stroke={colors.chart1}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                dot={false}
                activeDot={{ r: 4, fill: colors.chart1, stroke: colors.surface, strokeWidth: 2 }}
                isAnimationActive={true}
                animationDuration={300}
              />
              <Line
                type="monotone"
                dataKey="unique_chatter_count"
                name="Unique chatters"
                stroke={colors.chart2}
                strokeWidth={1.5}
                strokeLinecap="round"
                strokeLinejoin="round"
                dot={false}
                activeDot={{ r: 4, fill: colors.chart2, stroke: colors.surface, strokeWidth: 2 }}
                isAnimationActive={true}
                animationDuration={300}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
