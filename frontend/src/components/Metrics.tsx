import type React from 'react';
import { Activity, BarChart3, Hash, Users } from 'lucide-react';

type MetricsProps = {
  messageTotal: number;
  uniqueChatters: number;
  latestRate: number;
  peakMinute: number;
};

export function Metrics({ messageTotal, uniqueChatters, latestRate, peakMinute }: MetricsProps) {
  return (
    <section className="metrics">
      <Metric icon={<Activity size={18} />} label="Total Messages" value={messageTotal.toLocaleString()} />
      <Metric icon={<Users size={18} />} label="Recent Chatters" value={uniqueChatters.toLocaleString()} />
      <Metric icon={<Hash size={18} />} label="Latest Minute" value={latestRate.toLocaleString()} />
      <Metric icon={<BarChart3 size={18} />} label="Peak Minute" value={peakMinute.toLocaleString()} />
    </section>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      <div className="metric-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}
