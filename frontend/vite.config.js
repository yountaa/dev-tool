import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// В dev-режиме фронт ходит относительными путями, Vite проксирует их:
//   /silences, /victoria, … → FastAPI (localhost:8000)
//   /webhook → n8n (N8N_ORIGIN), для модуля alerts
// На проде то же делает nginx.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const n8nOrigin = env.N8N_ORIGIN || process.env.N8N_ORIGIN || 'http://localhost:5678'

  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/silences': 'http://localhost:8000',
        '/victoria': 'http://localhost:8000',
        '/alerts': 'http://localhost:8000',
        '/access': 'http://localhost:8000',
        '/health': 'http://localhost:8000',
        '/share': 'http://localhost:8000',
        '/s/': 'http://localhost:8000',
        '/webhook': {
          target: n8nOrigin,
          changeOrigin: true,
        },
      },
    },
  }
})
