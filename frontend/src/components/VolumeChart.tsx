import React from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { VolumePoint } from '../types';
import { formatMinuteTime } from '../utils/format';

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
  const chartData = React.useMemo(
    () =>
      fillAndClip(volume, windowMinutes).map((point) => ({
        ...point,
        label: formatMinuteTime(point.bucket)
      })),
    [volume, windowMinutes]
  );

  return (
    <div className="panel chart-panel">
      <div className="panel-heading">
        <h2>Messages Per Minute</h2>
        <span>last {windowLabel}</span>
      </div>
      {volume.length === 0 ? (
        <div className="chart-empty">No analytics data yet. Live messages will update this chart immediately.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData}>
            <CartesianGrid stroke="#2f2b3a" />
            <XAxis dataKey="label" tick={{ fill: '#adadb8', fontSize: 12 }} minTickGap={24} />
            <YAxis tick={{ fill: '#adadb8', fontSize: 12 }} allowDecimals={false} />
            <Tooltip
              contentStyle={{ background: '#18181b', border: '1px solid #9146ff' }}
              labelStyle={{ color: '#adadb8' }}
            />
            <Legend wrapperStyle={{ color: '#adadb8', fontSize: 13 }} iconType="plainline" />
            <Line
              type="monotone"
              dataKey="message_count"
              name="Messages"
              stroke="#9146ff"
              strokeWidth={2}
              dot={false}
              isAnimationActive={true}
              animationDuration={300}
            />
            <Line
              type="monotone"
              dataKey="unique_chatter_count"
              name="Unique chatters"
              stroke="#00f593"
              strokeWidth={2}
              dot={false}
              isAnimationActive={true}
              animationDuration={300}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
