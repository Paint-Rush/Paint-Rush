import { defineConfig } from '@playwright/test';

// Uses local HTML and intercepted responses. Does not start any server.
export default defineConfig({
  testDir: './e2e',
  timeout: 60000,
  workers: 1,
  use: {
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    launchOptions: { args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] },
  },
});
