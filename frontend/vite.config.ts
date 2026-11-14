import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

process.title = 'streamvault-web'

// https://vite.dev/config/
export default defineConfig({
  base: '/',
  envDir: '../',
  plugins: [
    react(),
    tailwindcss()
  ],
  server: {
    host: '127.0.0.1',
    port: 5173,
    allowedHosts: true, // Allows testing via Cloudflare Tunnel or LAN IP
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        xfwd: true,
        configure: (proxy, _options) => {
          proxy.on('proxyReq', (proxyReq, req, res) => {
            const url = req.url || '';
            if (/^\/api\/(system|aria2|admin|docs|redoc|openapi\.json)/i.test(url)) {
              res.writeHead(404, { 'Content-Type': 'application/json' });
              res.end(JSON.stringify({ detail: 'Not Found' }));
              return;
            }
            // Strip spoofable client IP headers in dev proxy
            proxyReq.removeHeader('cf-connecting-ip');
            proxyReq.removeHeader('x-real-ip');
          });
        },
      },
      '/media': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        xfwd: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/hls.js')) {
            return 'vendor-player';
          }
          if (id.includes('node_modules/@supabase')) {
            return 'vendor-supabase';
          }
          if (id.includes('node_modules/react') || id.includes('node_modules/react-dom') || id.includes('node_modules/scheduler')) {
            return 'vendor-react';
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'vendor-icons';
          }
        },
      },
    },
    chunkSizeWarningLimit: 600,
  },
})
