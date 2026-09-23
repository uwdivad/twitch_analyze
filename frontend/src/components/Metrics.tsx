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
    <section className="card stats metrics" aria-label="Key metrics">
      <Metric label="Total messages" value={messageTotal.toLocaleString()} detail={`stored · ${channelLabel}`} />
      <Metric label="Unique chatters" value={uniqueChatters.toLocaleString()} detail="in recent messages" />
      <Metric label="Latest minute" value={latestRate.toLocaleString()} detail="messages this minute" />
      <Metric label="Peak minute" value={peakMinute.toLocaleString()} detail={`msgs/min · last ${windowLabel}`} />
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <strong className="stat-value">{value}</strong>
      <small className="stat-detail">{detail}</small>
    </div>
  );
}
