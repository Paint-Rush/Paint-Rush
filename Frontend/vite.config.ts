import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    open: '/dev.html',
    proxy: { '/api': { target: 'http://127.0.0.1:8000' }, '/ws': { target: 'http://127.0.0.1:8000', ws: true }, '/characters': 'http://127.0.0.1:8000' },
  },
  preview: {
    proxy: { '/api': { target: 'http://127.0.0.1:8000' }, '/ws': { target: 'http://127.0.0.1:8000', ws: true }, '/characters': 'http://127.0.0.1:8000' },
  },
});
