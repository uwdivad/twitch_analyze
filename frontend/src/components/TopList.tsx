import type { TopItem } from '../types';

type TopListProps = {
  title: string;
  items: TopItem[];
};

export function TopList({ title, items }: TopListProps) {
  return (
    <div className="panel list-panel">
      <div className="panel-heading">
        <h2>{title}</h2>
      </div>
      <div className="top-list">
        {items.length === 0 ? (
          <div className="empty">No data yet.</div>
        ) : (
          items.map((item) => (
            <div key={item.value} className="top-item">
              <span>{item.value}</span>
              <strong>{item.count.toLocaleString()}</strong>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
