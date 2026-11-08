import { useMemo, useState } from 'react'
import { ErrorState } from '@/components/common'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Skeleton } from '@/components/ui/skeleton'
import { useBlobUrl } from '@/lib/hooks'
import type { Asset, Change, ComparisonDetail } from '@/lib/types'

export function AssetImage({
  asset,
  className,
  alt,
}: {
  asset: Asset | undefined
  className?: string
  alt?: string
}) {
  const [src, err] = useBlobUrl(asset?.url ?? null)
  if (!asset) return null
  if (err) return <ErrorState error={err} />
  if (!src) return <Skeleton className="aspect-video w-full" />
  return (
    <img
      src={src}
      alt={alt ?? asset.label}
      className={
        className ??
        'w-full rounded-md border border-line bg-[repeating-conic-gradient(#8882_0_25%,transparent_0_50%)] bg-[length:16px_16px]'
      }
      style={{ imageRendering: 'auto' }}
    />
  )
}

function Figure({ asset, children }: { asset: Asset | undefined; children?: React.ReactNode }) {
  if (!asset) return null
  return (
    <figure className="min-w-0">
      {children ?? <AssetImage asset={asset} />}
      <figcaption className="mt-1.5 text-[12px] text-fg-3">{asset.label}</figcaption>
    </figure>
  )
}

export function AssetGrid({ assets }: { assets: Asset[] }) {
  if (!assets.length) return null
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {assets.map((a) => (
        <Figure key={a.id} asset={a} />
      ))}
    </div>
  )
}

const byRole = (assets: Asset[], role: string) => assets.find((a) => a.role === role)

/** Side-by-side, heatmap, overlay and swipe views with changed-region boxes. */
export function ImageViewer({ detail }: { detail: ComparisonDetail }) {
  const assets = detail.result?.assets ?? []
  const [oldA, newA, heat, overlay] = ['old-preview', 'new-preview', 'heatmap', 'overlay'].map((r) =>
    byRole(assets, r),
  )
  const [swipe, setSwipe] = useState(50)
  const [oldSrc] = useBlobUrl(oldA?.url)
  const [newSrc] = useBlobUrl(newA?.url)
  const regions = useMemo(
    () => (detail.result?.changes ?? []).filter((c: Change) => c.path.startsWith('region[')),
    [detail.result],
  )
  const props = detail.result?.new.properties ?? {}
  const w = Number(props.width) || newA?.width || 1
  const h = Number(props.height) || newA?.height || 1
  const sameSize =
    detail.result?.old.properties.width === props.width &&
    detail.result?.old.properties.height === props.height

  return (
    <Tabs defaultValue="side">
      <TabsList>
        <TabsTrigger value="side">Side by side</TabsTrigger>
        {sameSize && <TabsTrigger value="swipe">Swipe</TabsTrigger>}
        {heat && <TabsTrigger value="heatmap">Heatmap</TabsTrigger>}
        {overlay && <TabsTrigger value="overlay">Overlay</TabsTrigger>}
      </TabsList>
      <TabsContent value="side">
        <div className="grid gap-4 md:grid-cols-2">
          <Figure asset={oldA} />
          <Figure asset={newA}>
            <div className="relative">
              <AssetImage asset={newA} />
              <svg
                viewBox={`0 0 ${w} ${h}`}
                className="pointer-events-none absolute inset-0 size-full"
                preserveAspectRatio="none"
                aria-hidden
              >
                {regions.map((r) => {
                  const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = (r.details.bbox as number[] | undefined) ?? []
                  return (
                    <rect
                      key={r.id}
                      x={x0}
                      y={y0}
                      width={x1 - x0}
                      height={y1 - y0}
                      fill="none"
                      stroke="#22d3ee"
                      strokeWidth={Math.max(1, w / 300)}
                      vectorEffect="non-scaling-stroke"
                    />
                  )
                })}
              </svg>
            </div>
          </Figure>
        </div>
        {regions.length > 0 && (
          <p className="mt-2 text-[12px] text-fg-3">{regions.length} changed region(s) outlined.</p>
        )}
      </TabsContent>
      {sameSize && (
        <TabsContent value="swipe">
          {oldSrc && newSrc ? (
            <div className="mx-auto max-w-2xl">
              <div className="relative overflow-hidden rounded-md border border-line">
                <img src={newSrc} alt="New" className="block w-full" />
                <img
                  src={oldSrc}
                  alt="Old"
                  className="absolute inset-0 block w-full"
                  style={{ clipPath: `inset(0 ${100 - swipe}% 0 0)` }}
                />
                <div className="absolute inset-y-0 w-px bg-accent" style={{ left: `${swipe}%` }} />
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={swipe}
                onChange={(e) => setSwipe(Number(e.target.value))}
                className="mt-3 w-full accent-[var(--accent)]"
                aria-label="Swipe between old (left) and new (right)"
              />
              <p className="flex justify-between text-[12px] text-fg-3">
                <span>old</span>
                <span>new</span>
              </p>
            </div>
          ) : (
            <Skeleton className="aspect-video" />
          )}
        </TabsContent>
      )}
      {heat && (
        <TabsContent value="heatmap">
          <div className="max-w-2xl">
            <Figure asset={heat} />
          </div>
          <p className="mt-2 text-[12px] text-fg-3">
            Black = unchanged, red → yellow → white = increasing channel difference.
          </p>
        </TabsContent>
      )}
      {overlay && (
        <TabsContent value="overlay">
          <div className="max-w-2xl">
            <Figure asset={overlay} />
          </div>
        </TabsContent>
      )}
    </Tabs>
  )
}

export function AudioViewer({ detail }: { detail: ComparisonDetail }) {
  const assets = detail.result?.assets ?? []
  const base = `/api/v1/comparisons/${detail.id}/files`
  const [oldSrc, oldErr] = useBlobUrl(detail.inputs_available ? `${base}/old` : null, 'audio/*')
  const [newSrc] = useBlobUrl(detail.inputs_available ? `${base}/new` : null, 'audio/*')
  return (
    <div className="space-y-4">
      <Figure asset={byRole(assets, 'waveform')} />
      <Figure asset={byRole(assets, 'spectrogram')} />
      {detail.inputs_available ? (
        <div className="grid gap-3 md:grid-cols-2">
          {[
            ['Old', oldSrc],
            ['New', newSrc],
          ].map(([label, src]) => (
            <div key={label}>
              <p className="mb-1 text-[12px] text-fg-3">{label}</p>
              {src ? <audio controls src={src} className="w-full" /> : <Skeleton className="h-10" />}
            </div>
          ))}
          {oldErr && <ErrorState error={oldErr} />}
        </div>
      ) : (
        <p className="text-[12px] text-fg-3">
          Playback unavailable: uploaded files were deleted by the retention policy.
        </p>
      )}
    </div>
  )
}

export function PdfViewer({ detail }: { detail: ComparisonDetail }) {
  const assets = detail.result?.assets ?? []
  const pages = [...new Set(assets.map((a) => a.role.match(/-page-(\d+)$/)?.[1]).filter(Boolean))] as string[]
  if (!pages.length) return <p className="text-sm text-fg-3">No rendered page differences.</p>
  return (
    <div className="space-y-6">
      {pages.map((n) => (
        <div key={n}>
          <p className="mb-2 text-[13px] font-medium">Page {n}</p>
          <div className="grid gap-3 md:grid-cols-3">
            {['old', 'new', 'overlay'].map((k) => (
              <Figure key={k} asset={byRole(assets, `${k}-page-${n}`)} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

/** Schema / data / statistics split for csv, parquet and sqlite. */
export function TabularViewer({ detail }: { detail: ComparisonDetail }) {
  const changes = detail.result?.changes ?? []
  const groups: [string, string, Change[]][] = [
    ['schema', 'Schema', changes.filter((c) => ['schema', 'structure', 'metadata'].includes(c.category))],
    ['data', 'Data', changes.filter((c) => c.category === 'data')],
    ['stats', 'Statistics', changes.filter((c) => c.category === 'statistics')],
  ]
  const first = groups.find((g) => g[2].length)?.[0] ?? 'schema'
  return (
    <Tabs defaultValue={first}>
      <TabsList>
        {groups.map(([k, label, items]) => (
          <TabsTrigger key={k} value={k}>
            {label} <span className="ml-1 text-fg-3 tabular-nums">{items.length}</span>
          </TabsTrigger>
        ))}
      </TabsList>
      {groups.map(([k, , items]) => (
        <TabsContent key={k} value={k}>
          {k === 'data' ? <RowDiffTable changes={items} /> : <SimpleList changes={items} />}
        </TabsContent>
      ))}
    </Tabs>
  )
}

function SimpleList({ changes }: { changes: Change[] }) {
  if (!changes.length) return <p className="text-sm text-fg-3">No changes in this view.</p>
  return (
    <ul className="divide-y divide-line rounded-lg border border-line">
      {changes.slice(0, 300).map((c) => (
        <li key={c.id} className="flex flex-wrap items-baseline gap-x-3 px-3 py-2 text-[13px]">
          <span className="font-mono text-[12.5px] text-fg-2">{c.path}</span>
          <span>{c.description}</span>
        </li>
      ))}
    </ul>
  )
}

function cell(v: unknown): string {
  return v === null || v === undefined ? '∅' : typeof v === 'string' ? v : JSON.stringify(v)
}

function RowDiffTable({ changes }: { changes: Change[] }) {
  if (!changes.length) return <p className="text-sm text-fg-3">No row-level changes listed.</p>
  return (
    <div className="overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-left text-[12.5px]">
        <thead className="border-b border-line bg-panel-2 text-fg-3">
          <tr>
            <th className="px-3 py-2 font-medium">Row</th>
            <th className="px-3 py-2 font-medium">Change</th>
            <th className="px-3 py-2 font-medium">Cells</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {changes.slice(0, 500).map((c) => {
            const o = (c.old_value ?? {}) as Record<string, unknown>
            const n = (c.new_value ?? {}) as Record<string, unknown>
            const cols = [...new Set([...Object.keys(o), ...Object.keys(n)])]
            return (
              <tr key={c.id} className="border-b border-line/70 align-top">
                <td className="px-3 py-1.5 whitespace-nowrap text-fg-2">{c.path}</td>
                <td
                  className={`px-3 py-1.5 font-sans ${c.kind === 'added' ? 'text-add' : c.kind === 'removed' ? 'text-remove' : 'text-modify'}`}
                >
                  {c.kind}
                </td>
                <td className="px-3 py-1.5">
                  <div className="flex flex-wrap gap-x-4 gap-y-0.5">
                    {cols.slice(0, 12).map((col) => (
                      <span key={col}>
                        <span className="text-fg-3">{col}=</span>
                        {c.kind === 'modified' ? (
                          <>
                            <span className="text-remove line-through decoration-remove/50">
                              {cell(o[col])}
                            </span>{' '}
                            <span className="text-add">{cell(n[col])}</span>
                          </>
                        ) : (
                          <span>{cell(c.kind === 'removed' ? o[col] : n[col])}</span>
                        )}
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
