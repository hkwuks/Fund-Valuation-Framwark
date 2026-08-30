import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    port: 3000,
    watch: {
      usePolling: true,
      interval: 1000
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          charts: ['lightweight-charts'],
        },
      },
    },
  },
})
