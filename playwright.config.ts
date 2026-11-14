import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export default defineConfig({
  testDir: './tests/browser', workers: 1, timeout: 60000,
  use: { baseURL: 'http://127.0.0.1:8001', viewport: { width: 1440, height: 1050 },
    launchOptions: existsSync(chrome) ? { executablePath: chrome } : {}, trace: 'retain-on-failure' },
  outputDir: 'artifacts/browser-results',
  reporter: [['list'], ['html', { outputFolder: 'artifacts/browser-report', open: 'never' }]],
  webServer: { command: '.venv/bin/python scripts/e2e_server.py', url: 'http://127.0.0.1:8001/api/health', reuseExistingServer: false, timeout: 30000 }
})
