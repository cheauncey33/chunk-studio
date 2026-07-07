import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: proxy API + crops to the FastAPI backend on :8000 (avoids CORS in dev).
// Prod: SPA is served from the FastAPI app, same-origin.
const apiTarget = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
      '/crops': apiTarget,
    },
  },
})
