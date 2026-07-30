import { defineConfig } from '@playwright/test';

/**
 * Browser smoke test. Unit tests and a successful production build do not
 * prove the app renders: a QR code shipped broken twice while both were
 * green. This drives a real browser against the built frontend served by the
 * backend, as the deployed image does.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.mjs',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env['CI'] ? 1 : 0,
  reporter: process.env['CI'] ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:8020',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // Chromium ships with the CI runner and the dev container image.
      executablePath: process.env['CHROME_BIN'] || undefined,
      args: ['--no-sandbox'],
    },
  },
  webServer: {
    command: 'sh e2e/serve.sh',
    url: 'http://127.0.0.1:8020/api/health',
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
