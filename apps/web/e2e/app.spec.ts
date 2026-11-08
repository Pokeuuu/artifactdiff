import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'

const examples = path.resolve(import.meta.dirname, '../../../examples')
const file = (fmt: string, name: string) => path.join(examples, fmt, name)

async function compare(page: Page, oldPath: string, newPath: string) {
  await page.goto('/compare')
  await page.getByTestId('drop-old-input').setInputFiles(oldPath)
  await page.getByTestId('drop-new-input').setInputFiles(newPath)
  await page.getByTestId('start-compare').click()
  await expect(page).toHaveURL(/\/comparisons\/[0-9a-f]{32}$/)
  await expect(page.getByText('Different').first()).toBeVisible({ timeout: 30_000 })
}

test('navigation, empty dashboard and theme toggle', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  await expect(page.getByText('Local API')).toBeVisible()
  for (const name of ['Compare', 'History', 'Plugins', 'Settings', 'Documentation']) {
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name }).click()
    await expect(
      page.getByRole('heading', { name: name === 'Documentation' ? 'Documentation' : name }),
    ).toBeVisible()
  }
  const html = page.locator('html')
  await expect(html).toHaveClass(/dark/)
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(html).toHaveClass(/light/)
})

test('plugins page lists real plugins', async ({ page }) => {
  await page.goto('/plugins')
  for (const p of ['json', 'csv', 'sqlite', 'safetensors', 'gltf'])
    await expect(page.getByTestId(`plugin-${p}`)).toBeVisible()
})

test('JSON comparison: changes table and Monaco diff', async ({ page }) => {
  await compare(page, file('json', 'old.json'), file('json', 'new.json'))
  const table = page.getByTestId('change-table')
  const epochs = table.getByRole('row').filter({ hasText: '$.training.epochs' })
  await expect(epochs).toContainText('type changed')
  await expect(epochs).toContainText('high')
  await table.getByText('$.model.name').click()
  await expect(page.getByText('Evidence:').first()).toBeVisible()
  await expect(page.getByTestId('monaco-diff')).toBeVisible({ timeout: 30_000 })
  await page.getByLabel('Filter changes').fill('tags')
  await expect(table.getByText('$.tags[1]')).toBeVisible()
  await expect(table.getByText('$.model.name')).toHaveCount(0)
})

test('image comparison shows visualizations', async ({ page }) => {
  await compare(page, file('image', 'old.png'), file('image', 'new.png'))
  await expect(page.getByRole('tab', { name: 'Heatmap' })).toBeVisible()
  await page.getByRole('tab', { name: 'Heatmap' }).click()
  await expect(page.getByAltText('Difference heatmap (max channel difference)')).toBeVisible()
})

test('3D comparison renders both models', async ({ page }) => {
  await compare(page, file('gltf', 'old.glb'), file('gltf', 'new.glb'))
  await expect(page.getByTestId('viewer-old').locator('canvas')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('viewer-new').locator('canvas')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Extra', exact: true })).toBeVisible()
})

test('CSV comparison: data view and report download', async ({ page }) => {
  await compare(page, file('csv', 'old.csv'), file('csv', 'new.csv'))
  await page.getByRole('tab', { name: /Data/ }).click()
  await expect(page.getByText("row[id='3']").first()).toBeVisible()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'HTML' }).click()
  expect((await download).suggestedFilename()).toMatch(/\.html$/)
})

test('history: search and delete', async ({ page }) => {
  await page.goto('/history')
  const table = page.getByTestId('history-table')
  await expect(table.getByRole('row')).not.toHaveCount(1)
  await page.getByLabel('Search history').fill('new.glb')
  await expect(table.getByRole('row')).toHaveCount(2)
  await page.getByRole('button', { name: 'Delete new.glb' }).click()
  await page.getByRole('button', { name: 'Delete', exact: true }).click()
  await expect(page.getByText('No matching comparisons')).toBeVisible()
  await page.goto('/')
  await expect(page.getByText('Comparisons per day')).toBeVisible()
})
