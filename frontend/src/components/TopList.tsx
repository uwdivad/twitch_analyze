import type { TopItem } from '../types';

type TopListProps = {
  title: string;
  items: TopItem[];
};

export function TopList({ title, items }: TopListProps) {
  return (
    <div className="card list-panel">
      <div className="card-heading">
        <h2>{title}</h2>
        {items.length > 0 ? <span className="card-meta">{items.length}</span> : null}
      </div>
      <div className="top-list">
        {items.length === 0 ? (
          <div className="empty">No data yet.</div>
        ) : (
          items.map((item, index) => (
            <div key={item.value} className="top-item">
              <span className="top-rank" aria-hidden="true">
                {index + 1}
              </span>
              <span className="top-value">{item.value}</span>
              <strong>{item.count.toLocaleString()}</strong>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
