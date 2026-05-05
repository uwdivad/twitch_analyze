import React from 'react';
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { VolumePoint } from '../types';
import { formatTime } from '../utils/format';

type VolumeChartProps = {
  volume: VolumePoint[];
  windowLabel: string;
};

export function VolumeChart({ volume, windowLabel }: VolumeChartProps) {
  const chartData = React.useMemo(
    () => volume.map((point) => ({ ...point, label: formatTime(point.bucket) })),
    [volume]
  );

  return (
    <div className="panel chart-panel">
      <div className="panel-heading">
        <h2>Messages Per Minute</h2>
        <span>{windowLabel}</span>
      </div>
      {volume.length === 0 ? (
        <div className="chart-empty">No analytics data yet. Live messages will update this chart immediately.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData}>
            <CartesianGrid stroke="#2f2b3a" />
            <XAxis dataKey="label" tick={{ fill: '#adadb8', fontSize: 12 }} minTickGap={24} />
            <YAxis tick={{ fill: '#adadb8', fontSize: 12 }} allowDecimals={false} />
            <Tooltip contentStyle={{ background: '#18181b', border: '1px solid #9146ff' }} />
            <Line type="monotone" dataKey="message_count" stroke="#9146ff" strokeWidth={2} dot />
            <Line type="monotone" dataKey="unique_chatter_count" stroke="#00f593" strokeWidth={2} dot />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
