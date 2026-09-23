import { Moon, Sun } from 'lucide-react';

import type { Theme } from '../../hooks/useTheme';

type ThemeToggleProps = {
  theme: Theme;
  onToggle: () => void;
};

export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  const next = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      aria-label={`Switch to ${next} theme`}
      className="btn btn-ghost btn-icon theme-toggle"
      onClick={onToggle}
      title={`Switch to ${next} theme`}
      type="button"
    >
      {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
    </button>
  );
}
