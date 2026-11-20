import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the web app runs on :5173 and forwards /api (including the host's
// WebSocket and the live-game event stream) to the backend on :8100, so the browser
// only ever talks to one origin - exactly as it does in production.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: process.env.API_TARGET ?? 'http://localhost:8100', ws: true, changeOrigin: false } },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
})
