import React from 'react';

import { THEME_CHANGE_EVENT } from './useTheme';

// Resolved token values for places that cannot use CSS variables directly
// (Recharts writes colors into SVG presentation attributes).
export type ThemeColors = {
  fg: string;
  fgMuted: string;
  fgDim: string;
  border: string;
  borderStrong: string;
  surface: string;
  accent: string;
  live: string;
  chart1: string;
  chart2: string;
};

const TOKENS: Record<keyof ThemeColors, string> = {
  fg: '--fg',
  fgMuted: '--fg-muted',
  fgDim: '--fg-dim',
  border: '--border',
  borderStrong: '--border-strong',
  surface: '--surface',
  accent: '--accent',
  live: '--live',
  chart1: '--chart-1',
  chart2: '--chart-2'
};

function readColors(): ThemeColors {
  const styles = getComputedStyle(document.documentElement);
  const colors = {} as ThemeColors;
  for (const key of Object.keys(TOKENS) as Array<keyof ThemeColors>) {
    colors[key] = styles.getPropertyValue(TOKENS[key]).trim();
  }
  return colors;
}

// Re-reads the tokens whenever the theme changes: listens for the useTheme event
// and also observes <html data-theme> so any other writer is picked up too.
export function useThemeColors(): ThemeColors {
  const [colors, setColors] = React.useState<ThemeColors>(readColors);

  React.useEffect(() => {
    const refresh = () => setColors(readColors());
    window.addEventListener(THEME_CHANGE_EVENT, refresh);
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => {
      window.removeEventListener(THEME_CHANGE_EVENT, refresh);
      observer.disconnect();
    };
  }, []);

  return colors;
}
