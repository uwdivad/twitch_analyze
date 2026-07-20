import React from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import type { ChannelInfo, ChannelVolumeSeries } from '../types';
import { formatMinuteTime } from '../utils/format';

type ChannelVolumeChartsProps = {
  channels: ChannelInfo[];
  channelVolumes: ChannelVolumeSeries;
  windowLabel: string;
};

export function ChannelVolumeCharts({ channels, channelVolumes, windowLabel }: ChannelVolumeChartsProps) {
  const channelChartData = React.useMemo(
    () =>
      channels
        .map((channel) => {
          const volume = channelVolumes[channel.channel_login] ?? [];
          return {
            channel,
            latestRate: volume.length > 0 ? volume[volume.length - 1].message_count : 0,
            peakRate: volume.reduce((max, point) => Math.max(max, point.message_count), 0),
            chartData: volume.map((point) => ({ ...point, label: formatMinuteTime(point.bucket) }))
          };
        })
        .sort(
          (a, b) =>
            Number(b.chartData.length > 0) - Number(a.chartData.length > 0) ||
            b.latestRate - a.latestRate ||
            b.peakRate - a.peakRate
        ),
    [channels, channelVolumes]
  );

  if (channels.length === 0) {
    return null;
  }

  return (
    <section className="channel-charts">
      <div className="section-heading">
        <h2>Channel Volume</h2>
        <span>last {windowLabel}</span>
      </div>
      <div className="channel-chart-grid">
        {channelChartData.map(({ channel, chartData, latestRate, peakRate }) => {
          return (
            <article
              key={channel.channel_login}
              className={`channel-chart-card${chartData.length === 0 ? ' is-empty' : ''}`}
            >
              <div className="channel-chart-heading">
                <strong>{channel.channel_display_name || channel.channel_login}</strong>
                {chartData.length > 0 ? (
                  <span>
                    {latestRate}/min <em>· peak {peakRate}</em>
                  </span>
                ) : null}
              </div>
              {chartData.length === 0 ? (
                <div className="channel-chart-empty">No data</div>
              ) : (
                <ResponsiveContainer width="100%" height={120}>
                  <LineChart data={chartData}>
                    <XAxis dataKey="label" hide />
                    <YAxis hide allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ background: '#18181b', border: '1px solid #9146ff' }}
                      labelStyle={{ color: '#adadb8' }}
                      formatter={(value) => [value ?? 0, 'Messages']}
                    />
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
