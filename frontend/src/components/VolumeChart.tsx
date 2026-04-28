import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { VolumePoint } from '../types';
import { formatTime } from '../utils/format';

type VolumeChartProps = {
  volume: VolumePoint[];
};

export function VolumeChart({ volume }: VolumeChartProps) {
  return (
    <div className="panel chart-panel">
      <div className="panel-heading">
        <h2>Messages Per Minute</h2>
      </div>
      {volume.length === 0 ? (
        <div className="chart-empty">No analytics data yet. Live messages will update this chart immediately.</div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={volume.map((point) => ({ ...point, label: formatTime(point.bucket) }))}>
            <CartesianGrid stroke="#243044" />
            <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 12 }} minTickGap={24} />
            <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} allowDecimals={false} />
            <Tooltip contentStyle={{ background: '#111827', border: '1px solid #334155' }} />
            <Line type="monotone" dataKey="message_count" stroke="#38bdf8" strokeWidth={2} dot />
            <Line type="monotone" dataKey="unique_chatter_count" stroke="#a3e635" strokeWidth={2} dot />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
