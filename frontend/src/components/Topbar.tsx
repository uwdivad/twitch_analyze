import type { Theme } from '../hooks/useTheme';
import type { AppView, SocketState } from '../types';
import { Segmented } from './ui/Segmented';
import { ThemeToggle } from './ui/ThemeToggle';

type TopbarProps = {
  socketState: SocketState;
  view: AppView;
  onViewChange: (view: AppView) => void;
  theme: Theme;
  onToggleTheme: () => void;
};

const VIEW_OPTIONS: ReadonlyArray<{ value: AppView; label: string }> = [
  { value: 'live', label: 'Live' },
  { value: 'vods', label: 'VODs' }
];

export function Topbar({ socketState, view, onViewChange, theme, onToggleTheme }: TopbarProps) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <a className="wordmark" href="#live" aria-label="Twitch Analyze home">
          <span className="wordmark-mark" aria-hidden="true">
            <svg viewBox="0 0 16 16" width="12" height="12">
              <path
                d="M2.5 11.5l3-5 3 3 5-7"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="wordmark-text">Twitch Analyze</span>
        </a>

        <nav className="topbar-nav" aria-label="Views">
          <Segmented ariaLabel="Views" options={VIEW_OPTIONS} value={view} onChange={onViewChange} />
        </nav>

        <div className="topbar-actions">
          <span className={`pill live-state ${socketState}`} role="status" title="Live feed connection">
            <span className={`dot is-${socketState}`} aria-hidden="true" />
            <span>{socketState}</span>
          </span>
          <ThemeToggle theme={theme} onToggle={onToggleTheme} />
        </div>
      </div>
    </header>
  );
}
