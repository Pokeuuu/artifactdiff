import type { ComparisonDetail } from '@/lib/types'

/** Whether the result page should show a format-specific visual comparison. */
export function hasViewer(detail: ComparisonDetail): boolean {
  const p = detail.plugin
  return (
    !!p &&
    (['json', 'text', 'csv', 'parquet', 'sqlite', 'image', 'audio', 'pdf', 'gltf', 'safetensors'].includes(
      p,
    ) ||
      (detail.result?.assets.length ?? 0) > 0)
  )
}
