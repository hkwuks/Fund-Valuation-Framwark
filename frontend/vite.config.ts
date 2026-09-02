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
        manualChunks: (id) => {
          if (id.includes('/node_modules/echarts/')) return 'echarts'
          if (id.includes('/node_modules/lightweight-charts/')) return 'charts'
          return undefined
        },
      },
    },
  },
})
