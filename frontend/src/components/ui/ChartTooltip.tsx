type TooltipEntry = {
  name?: string | number;
  value?: unknown;
  color?: string;
  dataKey?: unknown;
};

type ChartTooltipProps = {
  // Injected by Recharts when used as <Tooltip content={<ChartTooltip />} />.
  active?: boolean;
  payload?: ReadonlyArray<TooltipEntry>;
  label?: string | number;
  // Optional fixed series name when the chart has a single series.
  seriesName?: string;
};

function formatValue(value: unknown): string {
  return typeof value === 'number' ? value.toLocaleString() : String(value ?? 0);
}

// Recharts tooltip rendered as a .tooltip-card; series identity comes from the
// line key beside the text, never from coloring the text itself.
export function ChartTooltip({ active, payload, label, seriesName }: ChartTooltipProps) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  return (
    <div className="tooltip-card">
      {label !== undefined && label !== '' ? <div className="tooltip-title">{label}</div> : null}
      {payload.map((entry, index) => (
        <div key={`${String(entry.dataKey ?? entry.name)}-${index}`} className="tooltip-row">
          <span className="series-key" style={{ color: entry.color }} aria-hidden="true" />
          <span className="tooltip-name">{seriesName ?? entry.name}</span>
          <span className="tooltip-value">{formatValue(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}
