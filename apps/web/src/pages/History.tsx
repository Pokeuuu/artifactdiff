import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { History as HistoryIcon, Search, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, PageHeader, SeverityBadge, StatusBadge } from '@/components/common'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Input, Select } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { api, downloadReport } from '@/lib/api'
import { useDebounced } from '@/lib/hooks'
import { formatBytes, formatDate } from '@/lib/utils'

const PAGE = 25

export function HistoryPage() {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [plugin, setPlugin] = useState('')
  const [sort, setSort] = useState('-created_at')
  const [offset, setOffset] = useState(0)
  const [confirm, setConfirm] = useState<'all' | string | null>(null)
  const query = useDebounced(q)
  const params = { q: query, status, plugin, sort, limit: PAGE, offset }
  const list = useQuery({
    queryKey: ['comparisons', params],
    queryFn: () => api.comparisons(params),
    placeholderData: keepPreviousData,
    refetchInterval: (qr) =>
      qr.state.data?.items.some((i) => i.job_status === 'queued' || i.job_status === 'running')
        ? 1500
        : false,
  })
  const plugins = useQuery({ queryKey: ['plugins'], queryFn: api.plugins })
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['comparisons'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }
  const remove = useMutation({
    mutationFn: (id: string) => (id === 'all' ? api.clearHistory() : api.remove(id)),
    onSuccess: () => {
      setConfirm(null)
      invalidate()
    },
  })
  const exporter = useMutation({
    mutationFn: ({ id, f }: { id: string; f: 'html' | 'markdown' | 'json' }) => downloadReport(id, f),
  })

  const data = list.data
  const filtersActive = !!(query || status || plugin)

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="History"
        description="Comparisons stored on this server."
        actions={
          data &&
          data.total > 0 && (
            <Button variant="danger" size="sm" onClick={() => setConfirm('all')}>
              <Trash2 /> Clear history
            </Button>
          )
        }
      />
      <div className="mb-3 flex flex-wrap gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-fg-3" />
          <Input
            className="pl-8"
            placeholder="Search file names and summaries…"
            value={q}
            aria-label="Search history"
            onChange={(e) => {
              setQ(e.target.value)
              setOffset(0)
            }}
          />
        </div>
        <Select
          className="w-auto"
          value={status}
          aria-label="Status"
          onChange={(e) => {
            setStatus(e.target.value)
            setOffset(0)
          }}
        >
          <option value="">Any status</option>
          {['different', 'equivalent', 'identical', 'error', 'running', 'failed', 'cancelled'].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Select
          className="w-auto"
          value={plugin}
          aria-label="Plugin"
          onChange={(e) => {
            setPlugin(e.target.value)
            setOffset(0)
          }}
        >
          <option value="">Any plugin</option>
          {plugins.data?.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </Select>
        <Select className="w-auto" value={sort} aria-label="Sort" onChange={(e) => setSort(e.target.value)}>
          <option value="-created_at">Newest first</option>
          <option value="created_at">Oldest first</option>
          <option value="-total_changes">Most changes</option>
          <option value="name">File name</option>
        </Select>
      </div>
      {exporter.isError && <ErrorState error={exporter.error} className="mb-3" />}
      {list.isError && <ErrorState error={list.error} />}
      {!data && !list.isError && <Skeleton className="h-64" />}
      {data && data.total === 0 && (
        <EmptyState
          icon={HistoryIcon}
          title={filtersActive ? 'No matching comparisons' : 'No comparisons yet'}
        >
          {filtersActive ? (
            'Try other filters.'
          ) : (
            <Link to="/compare" className="text-accent hover:underline">
              Start a comparison
            </Link>
          )}
        </EmptyState>
      )}
      {data && data.total > 0 && (
        <>
          <div className="overflow-x-auto rounded-lg border border-line bg-panel">
            <table className="w-full min-w-[820px] text-left text-[13px]" data-testid="history-table">
              <thead className="border-b border-line bg-panel-2 text-[12px] text-fg-3">
                <tr>
                  <th className="px-3 py-2 font-medium">Files</th>
                  <th className="px-3 py-2 font-medium">Plugin</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 text-right font-medium">Changes</th>
                  <th className="px-3 py-2 font-medium">Severity</th>
                  <th className="px-3 py-2 font-medium">Date</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className="border-b border-line/70 last:border-0 hover:bg-panel-2/50">
                    <td className="max-w-[320px] px-3 py-2">
                      <Link
                        to={`/comparisons/${c.id}`}
                        className="block truncate font-mono text-[12.5px] hover:text-accent"
                      >
                        {c.old_name === c.new_name ? c.new_name : `${c.old_name} → ${c.new_name}`}
                      </Link>
                      <span className="text-[11.5px] text-fg-3">
                        {formatBytes(c.old_size)} → {formatBytes(c.new_size)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-fg-2">{c.plugin ?? '—'}</td>
                    <td className="px-3 py-2">
                      <StatusBadge job={c.job_status} result={c.result_status} />
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{c.total_changes.toLocaleString()}</td>
                    <td className="px-3 py-2">
                      <SeverityBadge severity={c.max_severity} />
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-fg-2">{formatDate(c.created_at)}</td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-1">
                        {c.job_status === 'completed' && (
                          <Select
                            className="h-8 w-[96px] text-[12.5px]"
                            value=""
                            aria-label={`Export ${c.new_name}`}
                            onChange={(e) =>
                              e.target.value && exporter.mutate({ id: c.id, f: e.target.value as 'html' })
                            }
                          >
                            <option value="">Export</option>
                            <option value="html">HTML</option>
                            <option value="markdown">Markdown</option>
                            <option value="json">JSON</option>
                          </Select>
                        )}
                        <Button
                          size="icon"
                          variant="ghost"
                          aria-label={`Delete ${c.new_name}`}
                          onClick={() => setConfirm(c.id)}
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex items-center justify-between text-[12.5px] text-fg-3">
            <span>
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total.toLocaleString()}
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
              >
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={offset + PAGE >= data.total}
                onClick={() => setOffset((o) => o + PAGE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm === 'all' ? 'Clear the entire history?' : 'Delete this comparison?'}
        description={
          confirm === 'all'
            ? 'Every comparison, visualization and retained upload on this server is deleted. This cannot be undone.'
            : 'The result, visualizations and retained uploads are deleted.'
        }
        confirmLabel={confirm === 'all' ? 'Clear history' : 'Delete'}
        busy={remove.isPending}
        onConfirm={() => confirm && remove.mutate(confirm)}
      />
    </div>
  )
}
