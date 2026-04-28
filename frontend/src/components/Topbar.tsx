import { Radio } from 'lucide-react';

import type { SocketState } from '../types';

type TopbarProps = {
  socketState: SocketState;
};

export function Topbar({ socketState }: TopbarProps) {
  return (
    <header className="topbar">
      <div>
        <h1>Twitch Analyze</h1>
        <p>Kafka-backed Twitch chat ingestion with ClickHouse analytics.</p>
      </div>
      <div className={`live-state ${socketState}`}>
        <Radio size={16} />
        <span>{socketState}</span>
      </div>
    </header>
  );
}
