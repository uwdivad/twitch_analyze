import React from 'react';

export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'twitch-analyze-theme';
// Dispatched on window whenever the applied theme changes (see useThemeColors).
export const THEME_CHANGE_EVENT = 'twitch-analyze:themechange';

function readStoredTheme(): Theme | null {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'light' || stored === 'dark' ? stored : null;
  } catch {
    return null;
  }
}

function systemTheme(): Theme {
  // Dark-first: only an explicit light preference yields light.
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

// The inline script in index.html sets data-theme before paint; this reads it back.
export function currentTheme(): Theme {
  const applied = document.documentElement.dataset.theme;
  if (applied === 'light' || applied === 'dark') {
    return applied;
  }
  return readStoredTheme() ?? systemTheme();
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (root.dataset.theme === theme) {
    return;
  }
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  window.dispatchEvent(new CustomEvent<Theme>(THEME_CHANGE_EVENT, { detail: theme }));
}

export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void; toggleTheme: () => void } {
  const [theme, setThemeState] = React.useState<Theme>(currentTheme);

  React.useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Follow the OS preference until the user picks a theme explicitly.
  React.useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: light)');
    if (!query) {
      return;
    }
    const handleChange = () => {
      if (readStoredTheme() === null) {
        setThemeState(query.matches ? 'light' : 'dark');
      }
    };
    query.addEventListener('change', handleChange);
    return () => query.removeEventListener('change', handleChange);
  }, []);

  const setTheme = React.useCallback((next: Theme) => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage can be unavailable (private mode); the theme still applies for this session.
    }
    setThemeState(next);
  }, []);

  const toggleTheme = React.useCallback(() => {
    setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}
