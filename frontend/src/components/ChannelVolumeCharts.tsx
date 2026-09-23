import React from 'react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { useThemeColors } from '../hooks/useThemeColors';
import type { ChannelInfo, ChannelVolumeSeries } from '../types';
import { formatMinuteTime } from '../utils/format';
import { ChartTooltip } from './ui/ChartTooltip';

type ChannelVolumeChartsProps = {
  channels: ChannelInfo[];
  channelVolumes: ChannelVolumeSeries;
  windowLabel: string;
};

export function ChannelVolumeCharts({ channels, channelVolumes, windowLabel }: ChannelVolumeChartsProps) {
  const colors = useThemeColors();
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
        <h2>Channel volume</h2>
        <span>messages per minute · last {windowLabel}</span>
      </div>
      <div className="channel-chart-grid">
        {channelChartData.map(({ channel, chartData, latestRate, peakRate }) => {
          const name = channel.channel_display_name || channel.channel_login;
          return (
            <article
              key={channel.channel_login}
              className={`card card-sm card-interactive channel-chart-card${chartData.length === 0 ? ' is-empty' : ''}`}
            >
              <div className="channel-chart-heading">
                <strong>{name}</strong>
                {chartData.length > 0 ? (
                  <span className="num">
                    {latestRate}/min <em>· peak {peakRate}</em>
                  </span>
                ) : null}
              </div>
              {chartData.length === 0 ? (
                <div className="channel-chart-empty">No data</div>
              ) : (
                <div
                  className="chart-frame"
                  role="img"
                  aria-label={`${name}: ${latestRate} messages in the latest minute, peak ${peakRate} per minute over the last ${windowLabel}.`}
                >
                  <ResponsiveContainer width="100%" height={96}>
                    <LineChart data={chartData} margin={{ top: 6, right: 4, bottom: 4, left: 4 }}>
                      <XAxis dataKey="label" hide />
                      <YAxis hide allowDecimals={false} />
                      <Tooltip
                        content={<ChartTooltip seriesName="Messages" />}
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
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
