import { HorizontalBars } from '@/components/charts'
import type { ComparisonDetail } from '@/lib/types'

/** Relative L2 difference per changed tensor (single series), plus header-level changes. */
export function TensorViewer({ detail }: { detail: ComparisonDetail }) {
  const metrics = detail.result?.statistics.metrics ?? {}
  const rel = (metrics.relative_difference_by_tensor ?? {}) as Record<string, number | null>
  const data = Object.entries(rel)
    .filter((e): e is [string, number] => typeof e[1] === 'number')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20)
    .map(([name, value]) => ({ name, value }))
  const header = (detail.result?.changes ?? []).filter((c) => c.category !== 'data')
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div>
        <p className="mb-2 text-[13px] font-medium">Relative L2 difference by tensor</p>
        {data.length ? (
          <HorizontalBars
            caption="Relative L2 difference by tensor"
            unit="(‖new−old‖ / ‖old‖)"
            data={data}
            format={(v) => v.toPrecision(3)}
          />
        ) : (
          <p className="text-sm text-fg-3">No tensor values changed.</p>
        )}
        <p className="mt-2 text-[12px] text-fg-3">
          {String(metrics.unchanged_tensors ?? 0)} tensor(s) byte-identical ·{' '}
          {String(metrics.old_tensors ?? '?')} → {String(metrics.new_tensors ?? '?')} tensors ·{' '}
          {Number(metrics.old_parameters ?? 0).toLocaleString()} →{' '}
          {Number(metrics.new_parameters ?? 0).toLocaleString()} parameters
        </p>
      </div>
      <div>
        <p className="mb-2 text-[13px] font-medium">Header changes</p>
        <ul className="divide-y divide-line rounded-lg border border-line text-[13px]">
          {header.length ? (
            header.slice(0, 100).map((c) => (
              <li key={c.id} className="px-3 py-2">
                <span className="font-mono text-[12.5px] text-fg-2">{c.path}</span>
                <p>{c.description}</p>
              </li>
            ))
          ) : (
            <li className="px-3 py-2 text-fg-3">No structural changes.</li>
          )}
        </ul>
      </div>
    </div>
  )
}
