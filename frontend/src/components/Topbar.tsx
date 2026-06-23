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
        <p>View live aggregated chat data.</p>
      </div>
      <div className={`live-state ${socketState}`}>
        <Radio size={16} />
        <span>{socketState}</span>
      </div>
    </header>
  );
}
