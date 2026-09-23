import React from 'react';
import ReactDOM from 'react-dom/client';

// Global styles first so feature CSS imported by components (e.g. vod.css) wins
// over equal-specificity primitives.
import '@fontsource-variable/inter';
import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';
import './styles/layout.css';
import { App } from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
