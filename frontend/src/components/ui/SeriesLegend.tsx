type SeriesLegendItem = {
  label: string;
  color: string;
};

// Inline legend pills for a card heading (replaces the Recharts <Legend>).
export function SeriesLegend({ items }: { items: ReadonlyArray<SeriesLegendItem> }) {
  return (
    <ul aria-label="Legend" className="series-legend">
      {items.map((item) => (
        <li key={item.label} className="pill pill-sm">
          <span aria-hidden="true" className="series-key" style={{ color: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
