import os from 'node:os'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// Boots the real API (serving the built UI from apps/web/dist) against a throwaway data directory.
const python =
  process.env.PYTHON ??
  path.resolve(process.platform === 'win32' ? '../../.venv/Scripts/python.exe' : '../../.venv/bin/python')
const port = Number(process.env.E2E_PORT ?? 8765)
const dataDir = path.join(os.tmpdir(), `artifactdiff-e2e-${Date.now()}`)

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: { baseURL: `http://127.0.0.1:${port}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `"${python}" -m artifactdiff_api --port ${port}`,
    url: `http://127.0.0.1:${port}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      ARTIFACTDIFF_DATA_DIR: dataDir,
      ARTIFACTDIFF_WEB_DIR: path.resolve('dist'),
      ARTIFACTDIFF_ISOLATION: 'inline',
      ARTIFACTDIFF_RATE_LIMIT_PER_MINUTE: '0',
    },
  },
})
