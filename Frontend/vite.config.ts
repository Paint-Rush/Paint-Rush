import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    open: '/dev.html',
    proxy: { '/api': { target: 'http://127.0.0.1:8000', ws: true } },
  },
  preview: {
    proxy: { '/api': { target: 'http://127.0.0.1:8000', ws: true } },
  },
});
