import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// В dev-режиме фронт ходит на /silences, а Vite проксирует это на бэкенд —
// так не нужно думать про CORS и адреса. На проде то же делает nginx.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/silences': 'http://localhost:8000',
      '/victoria': 'http://localhost:8000',
      '/access': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/share': 'http://localhost:8000',   // короткие ссылки: создать
      '/s/': 'http://localhost:8000',      // короткие ссылки: перейти
    },
  },
})
