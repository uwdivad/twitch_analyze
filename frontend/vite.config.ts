import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.VITE_BACKEND_PROXY_TARGET ?? 'http://localhost:8000',
      '/health': process.env.VITE_BACKEND_PROXY_TARGET ?? 'http://localhost:8000',
      '/ws': {
        target: process.env.VITE_BACKEND_WS_PROXY_TARGET ?? 'ws://localhost:8000',
        ws: true
      }
    }
  }
});
