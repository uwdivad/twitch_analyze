import type React from 'react';
import { Activity, BarChart3, Hash, Users } from 'lucide-react';

type MetricsProps = {
  messageTotal: number;
  uniqueChatters: number;
  latestRate: number;
  peakMinute: number;
  channelLabel: string;
  windowLabel: string;
};

export function Metrics({
  messageTotal,
  uniqueChatters,
  latestRate,
  peakMinute,
  channelLabel,
  windowLabel
}: MetricsProps) {
  return (
    <section className="metrics">
      <Metric
        icon={<Activity size={18} />}
        label="Total Messages"
        value={messageTotal.toLocaleString()}
        detail={`stored · ${channelLabel}`}
      />
      <Metric
        icon={<Users size={18} />}
        label="Unique Chatters"
        value={uniqueChatters.toLocaleString()}
        detail="in recent messages"
      />
      <Metric
        icon={<Hash size={18} />}
        label="Latest Minute"
        value={latestRate.toLocaleString()}
        detail="messages this minute"
      />
      <Metric
        icon={<BarChart3 size={18} />}
        label="Peak Minute"
        value={peakMinute.toLocaleString()}
        detail={`msgs/min · last ${windowLabel}`}
      />
    </section>
  );
}

function Metric({
  icon,
  label,
  value,
  detail
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="metric">
      <div className="metric-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}
