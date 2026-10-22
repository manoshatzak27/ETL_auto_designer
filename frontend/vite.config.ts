import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8000'

// Inside Docker the source is a bind mount, and filesystem events don't cross
// that boundary — not from a Windows host into a Linux container, and not
// through Docker Desktop's VirtioFS on Linux either. Without polling, Vite
// simply never learns that a file changed: it keeps serving the transform it
// cached at startup, so a host-side edit appears to do nothing until the
// container is restarted.
//
// docker-compose.yml sets these two variables for exactly this reason. They
// used to work on their own, back when Vite watched through chokidar and
// chokidar read them from the environment; Vite's own watcher does not, so
// they have to be passed through here.
const usePolling = process.env.CHOKIDAR_USEPOLLING === 'true'
const pollInterval = Number(process.env.CHOKIDAR_INTERVAL) || 500

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: true,
    ...(usePolling ? { watch: { usePolling: true, interval: pollInterval } } : {}),
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
