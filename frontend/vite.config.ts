import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.VITE_BACKEND_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Allow HTTPS access through `tailscale serve` (*.ts.net); raw IPs are always allowed.
    allowedHosts: ['.ts.net'],
    // xfwd adds X-Forwarded-For so backend logs show the browser's IP instead of
    // 127.0.0.1 (uvicorn trusts that header from localhost, i.e. from this proxy).
    proxy: {
      '/api': { target: backendTarget, xfwd: true },
      '/health': { target: backendTarget, xfwd: true }
    }
  }
});
