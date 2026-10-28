import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    proxy: {
      '/health': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/services': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/experiments': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/scenarios': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/dependencies': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/investigations': process.env.CONTROL_API_PROXY ?? 'http://127.0.0.1:8000',
      '/orders': process.env.ORDER_API_PROXY ?? 'http://127.0.0.1:8001',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    css: true,
  },
})
