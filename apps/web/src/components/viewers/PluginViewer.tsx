import { lazy, Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import type { ComparisonDetail } from '@/lib/types'
import { AssetGrid, AudioViewer, ImageViewer, PdfViewer, TabularViewer } from './AssetViewers'
import { TensorViewer } from './TensorViewer'

// Heavy viewers load on demand so the main bundle stays small.
const TextDiffViewer = lazy(() => import('./TextDiffViewer'))
const ModelViewer = lazy(() => import('./ModelViewer'))

export function PluginViewer({ detail }: { detail: ComparisonDetail }) {
  const fallback = <Skeleton className="h-[360px]" />
  switch (detail.plugin) {
    case 'json':
    case 'text':
      return (
        <Suspense fallback={fallback}>
          <TextDiffViewer detail={detail} />
        </Suspense>
      )
    case 'csv':
      return (
        <div className="space-y-6">
          <TabularViewer detail={detail} />
          <details>
            <summary className="cursor-pointer text-[13px] text-fg-2">Raw text diff</summary>
            <div className="mt-3">
              <Suspense fallback={fallback}>
                <TextDiffViewer detail={detail} />
              </Suspense>
            </div>
          </details>
        </div>
      )
    case 'parquet':
    case 'sqlite':
      return <TabularViewer detail={detail} />
    case 'image':
      return <ImageViewer detail={detail} />
    case 'audio':
      return <AudioViewer detail={detail} />
    case 'pdf':
      return <PdfViewer detail={detail} />
    case 'gltf':
      return (
        <div className="space-y-4">
          <Suspense fallback={fallback}>
            <ModelViewer detail={detail} />
          </Suspense>
          <AssetGrid assets={detail.result?.assets ?? []} />
        </div>
      )
    case 'safetensors':
      return <TensorViewer detail={detail} />
    default:
      return <AssetGrid assets={detail.result?.assets ?? []} />
  }
}
