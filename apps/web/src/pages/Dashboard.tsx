import { useQuery } from '@tanstack/react-query'
import { ArrowRight, GitCompareArrows } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ActivityChart, HorizontalBars } from '@/components/charts'
import {
  EmptyState,
  ErrorState,
  KindBadge,
  PageHeader,
  SeverityBadge,
  Stat,
  StatusBadge,
} from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { formatBytes, formatNumber, timeAgo } from '@/lib/utils'

export function Dashboard() {
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 15_000 })
  const recent = useQuery({
    queryKey: ['comparisons', 'recent'],
    queryFn: () => api.comparisons({ limit: 6 }),
  })

  const action = (
    <Button asChild>
      <Link to="/compare">
        <GitCompareArrows /> New comparison
      </Link>
    </Button>
  )

  if (stats.isError) return <ErrorState error={stats.error} />
  const s = stats.data

  return (
    <div className="animate-fade-in">
      <PageHeader title="Dashboard" description="Activity of this ArtifactDiff server." actions={action} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {s ? (
          <>
            <Stat label="Comparisons" value={formatNumber(s.total_comparisons)} hint={`${s.failed} failed`} />
            <Stat label="Files analyzed" value={formatNumber(s.files_analyzed)} />
            <Stat label="Data analyzed" value={formatBytes(s.bytes_analyzed)} />
            <Stat label="Changes found" value={formatNumber(s.total_changes)} />
            <Stat
              label="Supported formats"
              value={s.supported_formats}
              hint={`${s.supported_extensions.length} extensions`}
            />
          </>
        ) : (
          Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[76px]" />)
        )}
      </div>

      {s && s.total_comparisons === 0 ? (
        <div className="mt-6">
          <EmptyState icon={GitCompareArrows} title="No comparisons yet">
            Compare two versions of a dataset, model, image, database or document to see what changed.
            <div className="mt-4">{action}</div>
          </EmptyState>
        </div>
      ) : (
        <>
          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Comparisons per day</CardTitle>
                <span className="text-[12px] text-fg-3">last 30 days</span>
              </CardHeader>
              <CardContent>
                {s ? <ActivityChart data={s.activity} /> : <Skeleton className="h-48" />}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Comparisons by plugin</CardTitle>
              </CardHeader>
              <CardContent>
                {s && s.plugin_usage.length > 0 ? (
                  <HorizontalBars
                    caption="Comparisons by plugin"
                    unit="comparisons"
                    data={s.plugin_usage.slice(0, 8).map((p) => ({ name: p.plugin, value: p.comparisons }))}
                  />
                ) : s ? (
                  <p className="text-sm text-fg-3">No completed comparisons.</p>
                ) : (
                  <Skeleton className="h-40" />
                )}
              </CardContent>
            </Card>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Recent comparisons</CardTitle>
                <Link
                  to="/history"
                  className="inline-flex items-center gap-1 text-[12.5px] text-accent hover:underline"
                >
                  History <ArrowRight className="size-3.5" />
                </Link>
              </CardHeader>
              <CardContent className="px-0 pb-1">
                {recent.isError && <ErrorState error={recent.error} className="mx-4 mb-3" />}
                <ul className="divide-y divide-line">
                  {recent.data?.items.map((c) => (
                    <li key={c.id}>
                      <Link
                        to={`/comparisons/${c.id}`}
                        className="flex items-center gap-3 px-4 py-2.5 hover:bg-panel-2/60"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-mono text-[13px]">
                            {c.old_name === c.new_name ? c.new_name : `${c.old_name} → ${c.new_name}`}
                          </p>
                          <p className="truncate text-[12px] text-fg-3">
                            {c.plugin ?? 'pending'} · {timeAgo(c.created_at)}
                            {c.summary ? ` · ${c.summary}` : ''}
                          </p>
                        </div>
                        <StatusBadge job={c.job_status} result={c.result_status} />
                      </Link>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Recent notable changes</CardTitle>
                </CardHeader>
                <CardContent>
                  {s?.recent_changes.length ? (
                    <ul className="space-y-2.5">
                      {s.recent_changes.slice(0, 6).map((c, i) => (
                        <li key={`${c.comparison_id}-${i}`}>
                          <Link to={`/comparisons/${c.comparison_id}`} className="block hover:opacity-80">
                            <div className="flex items-center gap-1.5">
                              <KindBadge kind={c.kind} />
                              <SeverityBadge severity={c.severity} />
                            </div>
                            <p className="mt-1 truncate text-[13px]">{c.description}</p>
                            <p className="truncate font-mono text-[11.5px] text-fg-3">
                              {c.file} · {c.path}
                            </p>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-fg-3">Nothing yet.</p>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Storage</CardTitle>
                </CardHeader>
                <CardContent className="text-[13px]">
                  {s ? (
                    <dl className="grid grid-cols-2 gap-y-1">
                      <dt className="text-fg-3">Retained uploads</dt>
                      <dd className="text-right tabular-nums">{formatBytes(s.storage.inputs)}</dd>
                      <dt className="text-fg-3">Visualizations</dt>
                      <dd className="text-right tabular-nums">{formatBytes(s.storage.assets)}</dd>
                    </dl>
                  ) : (
                    <Skeleton className="h-10" />
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
