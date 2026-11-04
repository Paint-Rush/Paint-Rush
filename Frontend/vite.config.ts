import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    open: '/dev.html',
    proxy: { '/api': 'http://127.0.0.1:8000', '/users': 'http://127.0.0.1:8000', '/characters': 'http://127.0.0.1:8000' },
  },
  preview: {
    proxy: { '/api': 'http://127.0.0.1:8000', '/users': 'http://127.0.0.1:8000', '/characters': 'http://127.0.0.1:8000' },
  },
});
