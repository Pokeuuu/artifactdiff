import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Download, Loader2, Square, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ChangeTable } from '@/components/ChangeTable'
import { Callout, ErrorState, KeyValue, SeverityBadge, Stat, StatusBadge } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { hasViewer } from '@/components/viewers/hasViewer'
import { PluginViewer } from '@/components/viewers/PluginViewer'
import { ApiError, api, downloadReport } from '@/lib/api'
import type { ArtifactMetadata, ComparisonDetail } from '@/lib/types'
import { formatBytes, formatDate, formatDuration, formatValue } from '@/lib/utils'

export function ResultPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const q = useQuery({
    queryKey: ['comparison', id],
    queryFn: () => api.comparison(id),
    refetchInterval: (query) => {
      const s = query.state.data?.job_status
      return s === 'queued' || s === 'running' ? 750 : false
    },
  })
  const cancel = useMutation({ mutationFn: () => api.cancel(id), onSettled: () => q.refetch() })
  const remove = useMutation({
    mutationFn: () => api.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['comparisons'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      navigate('/history')
    },
  })
  const download = useMutation({ mutationFn: (f: 'html' | 'markdown' | 'json') => downloadReport(id, f) })

  if (q.isError)
    return q.error instanceof ApiError && q.error.status === 404 ? (
      <ErrorState error="This comparison does not exist or was deleted." />
    ) : (
      <ErrorState error={q.error} />
    )
  if (!q.data) return <Skeleton className="h-64" />
  const d = q.data
  const r = d.result
  const pending = d.job_status === 'queued' || d.job_status === 'running'

  return (
    <div className="animate-fade-in">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge job={d.job_status} result={d.result_status} />
            {d.plugin && (
              <span className="text-[12.5px] text-fg-3">
                {d.plugin} {d.plugin_version} · {formatDuration(d.duration_ms)} · {formatDate(d.created_at)}
              </span>
            )}
          </div>
          <h1 className="mt-2 flex flex-wrap items-center gap-2 font-mono text-lg font-semibold break-all">
            <span>{d.old_name}</span>
            <ArrowRight className="size-4 shrink-0 text-fg-3" />
            <span>{d.new_name}</span>
          </h1>
          {d.summary && <p className="mt-1 text-sm text-fg-2">{d.summary}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {r &&
            (['html', 'markdown', 'json'] as const).map((f) => (
              <Button
                key={f}
                size="sm"
                variant="secondary"
                onClick={() => download.mutate(f)}
                disabled={download.isPending}
              >
                <Download /> {f === 'markdown' ? 'Markdown' : f.toUpperCase()}
              </Button>
            ))}
          <Button size="sm" variant="ghost" onClick={() => setConfirm(true)} aria-label="Delete comparison">
            <Trash2 />
          </Button>
        </div>
      </div>
      {download.isError && <ErrorState error={download.error} className="mb-4" />}

      {pending && (
        <Card className="mb-4">
          <CardContent className="pt-4">
            <div className="flex items-center gap-3">
              <Loader2 className="size-4 animate-spin text-accent" />
              <p className="flex-1 text-sm">{d.progress_message ?? 'Waiting for a worker…'}</p>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
              >
                <Square /> Cancel
              </Button>
            </div>
            <div
              className="mt-3 h-1.5 overflow-hidden rounded-full bg-panel-2"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round((d.progress ?? 0) * 100)}
            >
              <div
                className="h-full rounded-full bg-accent transition-[width]"
                style={{ width: `${Math.max(3, (d.progress ?? 0) * 100)}%` }}
              />
            </div>
          </CardContent>
        </Card>
      )}
      {d.error && <ErrorState error={d.error} className="mb-4" />}

      {r && <ResultBody detail={d} />}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Delete this comparison?"
        description="The result, its visualizations and any retained uploads are removed from this server."
        confirmLabel="Delete"
        onConfirm={() => remove.mutate()}
        busy={remove.isPending}
      />
    </div>
  )
}

function ResultBody({ detail }: { detail: ComparisonDetail }) {
  const r = detail.result!
  const s = r.statistics
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat
          label="Changes"
          value={s.total_changes.toLocaleString()}
          hint={
            s.reported_changes < s.total_changes ? `${s.reported_changes.toLocaleString()} listed` : undefined
          }
        />
        <Stat label="Highest severity" value={<SeverityBadge severity={s.max_severity} />} />
        <Stat label="By kind" value={<Breakdown data={s.by_kind} />} />
        <Stat label="By category" value={<Breakdown data={s.by_category} />} />
      </div>

      {(r.warnings.length > 0 || r.limitations.length > 0) && (
        <div className="grid gap-3 md:grid-cols-2">
          {r.warnings.length > 0 && (
            <Callout tone="warn" title="Warnings">
              <ul className="mt-1 space-y-1">
                {r.warnings.map((w) => (
                  <li key={w.code + w.message}>
                    <span className="font-mono text-[12px] text-fg-3">{w.code}</span> — {w.message}
                  </li>
                ))}
              </ul>
            </Callout>
          )}
          {r.limitations.length > 0 && (
            <Callout tone="info" title="Limitations of this comparison">
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {r.limitations.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
      )}

      {hasViewer(detail) && (
        <Card>
          <CardHeader>
            <CardTitle>Visual comparison</CardTitle>
          </CardHeader>
          <CardContent>
            <PluginViewer detail={detail} />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Changes</CardTitle>
        </CardHeader>
        <CardContent>
          {r.changes.length ? (
            <ChangeTable changes={r.changes} total={s.total_changes} />
          ) : (
            <p className="text-sm text-fg-3">
              {r.status === 'identical'
                ? 'The files are byte-for-byte identical.'
                : r.status === 'equivalent'
                  ? 'The bytes differ, but the plugin found no meaningful change.'
                  : 'No changes.'}
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <MetaCard title="Old" meta={r.old} />
        <MetaCard title="New" meta={r.new} />
      </div>

      {Object.keys(s.metrics).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Measurements</CardTitle>
          </CardHeader>
          <CardContent>
            <KeyValue
              data={Object.fromEntries(Object.entries(s.metrics).map(([k, v]) => [k, formatValue(v, 200)]))}
            />
          </CardContent>
        </Card>
      )}

      <details className="rounded-lg border border-line bg-panel px-4 py-3">
        <summary className="cursor-pointer text-[13px] font-medium">Reproducibility &amp; options</summary>
        <pre className="mt-3 overflow-x-auto font-mono text-[12px] text-fg-2">
          {JSON.stringify({ fingerprint: r.fingerprint, options: r.options, ...r.reproducibility }, null, 2)}
        </pre>
      </details>
      <p className="text-[12px] text-fg-3">
        Not what you expected?{' '}
        <Link to="/compare" className="text-accent hover:underline">
          Run another comparison
        </Link>{' '}
        with different options.
      </p>
    </div>
  )
}

function Breakdown({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data)
  if (!entries.length) return <span className="text-fg-3">—</span>
  return (
    <span className="block text-[12.5px] leading-5 font-normal text-fg-2">
      {entries.map(([k, v]) => (
        <span key={k} className="mr-2 inline-block whitespace-nowrap">
          {k.replace('_', ' ')} <b className="text-fg">{v.toLocaleString()}</b>
        </span>
      ))}
    </span>
  )
}

function MetaCard({ title, meta }: { title: string; meta: ArtifactMetadata }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex min-w-0 items-center gap-2">
          {title}
          <span className="truncate font-mono font-normal text-fg-2">{meta.name}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="mb-3 font-mono text-[12px] text-fg-3">
          {formatBytes(meta.size_bytes)} · sha256 {meta.sha256.slice(0, 16)}… ·{' '}
          {meta.format?.name ?? 'unknown'}
          {meta.detected_by && ` (by ${meta.detected_by})`}
        </p>
        <KeyValue data={meta.properties} />
      </CardContent>
    </Card>
  )
}
