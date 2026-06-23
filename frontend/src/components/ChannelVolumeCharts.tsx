import React from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { ChannelInfo, ChannelVolumeSeries } from '../types';
import { formatTime } from '../utils/format';

type ChannelVolumeChartsProps = {
  channels: ChannelInfo[];
  channelVolumes: ChannelVolumeSeries;
  windowLabel: string;
};

export function ChannelVolumeCharts({ channels, channelVolumes, windowLabel }: ChannelVolumeChartsProps) {
  const channelChartData = React.useMemo(
    () =>
      channels.map((channel) => {
        const volume = channelVolumes[channel.channel_login] ?? [];
        return {
          channel,
          latestRate: volume.length > 0 ? volume[volume.length - 1].message_count : 0,
          chartData: volume.map((point) => ({ ...point, label: formatTime(point.bucket) }))
        };
      }),
    [channels, channelVolumes]
  );

  if (channels.length === 0) {
    return null;
  }

  return (
    <section className="channel-charts">
      <div className="section-heading">
        <h2>Channel Volume</h2>
        <span>{windowLabel}</span>
      </div>
      <div className="channel-chart-grid">
        {channelChartData.map(({ channel, chartData, latestRate }) => {
          return (
            <article key={channel.channel_login} className="channel-chart-card">
              <div className="channel-chart-heading">
                <strong>{channel.channel_display_name || channel.channel_login}</strong>
                <span>{latestRate}/min</span>
              </div>
              {chartData.length === 0 ? (
                <div className="channel-chart-empty">No data</div>
              ) : (
                <ResponsiveContainer width="100%" height={120}>
                  <LineChart data={chartData}>
                    <XAxis dataKey="label" hide />
                    <YAxis hide allowDecimals={false} />
                    <Tooltip contentStyle={{ background: '#18181b', border: '1px solid #9146ff' }} />
                    <Line
                      type="monotone"
                      dataKey="message_count"
                      stroke="#bf94ff"
                      strokeWidth={2}
                      dot={false}
                      isAnimationActive={true}
                      animationDuration={300}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
