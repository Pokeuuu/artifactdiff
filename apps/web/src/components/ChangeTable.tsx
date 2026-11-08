import { ChevronRight, Search } from 'lucide-react'
import { Fragment, useMemo, useState } from 'react'
import { KindBadge, SeverityBadge } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Input, Select } from '@/components/ui/input'
import type { Change } from '@/lib/types'
import { cn, formatValue, pretty } from '@/lib/utils'

const PAGE = 200

export function ChangeTable({ changes, total }: { changes: Change[]; total: number }) {
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [category, setCategory] = useState('')
  const [severity, setSeverity] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [limit, setLimit] = useState(PAGE)

  const kinds = useMemo(() => [...new Set(changes.map((c) => c.kind))].sort(), [changes])
  const categories = useMemo(() => [...new Set(changes.map((c) => c.category))].sort(), [changes])
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return changes.filter(
      (c) =>
        (!kind || c.kind === kind) &&
        (!category || c.category === category) &&
        (!severity || c.severity === severity) &&
        (!needle ||
          c.path.toLowerCase().includes(needle) ||
          c.description.toLowerCase().includes(needle) ||
          formatValue(c.old_value, 400).toLowerCase().includes(needle) ||
          formatValue(c.new_value, 400).toLowerCase().includes(needle)),
    )
  }, [changes, q, kind, category, severity])

  const toggle = (id: string) =>
    setOpen((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-fg-3" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by path, description or value…"
            className="pl-8"
            aria-label="Filter changes"
          />
        </div>
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="w-auto" aria-label="Kind">
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </Select>
        <Select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="w-auto"
          aria-label="Category"
        >
          <option value="">All categories</option>
          {categories.map((k) => (
            <option key={k}>{k}</option>
          ))}
        </Select>
        <Select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className="w-auto"
          aria-label="Severity"
        >
          <option value="">All severities</option>
          {['high', 'medium', 'low', 'info'].map((k) => (
            <option key={k}>{k}</option>
          ))}
        </Select>
      </div>
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[760px] text-left text-[13px]" data-testid="change-table">
          <thead className="border-b border-line bg-panel-2 text-[12px] text-fg-3">
            <tr>
              <th className="w-8" />
              <th className="px-3 py-2 font-medium">Kind</th>
              <th className="px-3 py-2 font-medium">Path</th>
              <th className="px-3 py-2 font-medium">Change</th>
              <th className="px-3 py-2 font-medium">Old</th>
              <th className="px-3 py-2 font-medium">New</th>
              <th className="px-3 py-2 font-medium">Severity</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, limit).map((c) => {
              const isOpen = open.has(c.id)
              return (
                <Fragment key={c.id}>
                  <tr
                    className={cn(
                      'cursor-pointer border-b border-line/70 hover:bg-panel-2/60',
                      isOpen && 'bg-panel-2/60',
                    )}
                    onClick={() => toggle(c.id)}
                    aria-expanded={isOpen}
                  >
                    <td className="pl-2 text-fg-3">
                      <ChevronRight className={cn('size-4 transition-transform', isOpen && 'rotate-90')} />
                    </td>
                    <td className="px-3 py-2">
                      <KindBadge kind={c.kind} />
                    </td>
                    <td className="max-w-[260px] truncate px-3 py-2 font-mono text-[12.5px]" title={c.path}>
                      {c.path}
                    </td>
                    <td className="px-3 py-2">
                      {c.description}
                      {c.confidence !== null && (
                        <span className="ml-1.5 text-[11.5px] text-fg-3">
                          (confidence {c.confidence.toFixed(2)})
                        </span>
                      )}
                    </td>
                    <td className="max-w-[200px] truncate px-3 py-2 font-mono text-[12px] text-remove/90">
                      {formatValue(c.old_value, 60)}
                    </td>
                    <td className="max-w-[200px] truncate px-3 py-2 font-mono text-[12px] text-add">
                      {formatValue(c.new_value, 60)}
                    </td>
                    <td className="px-3 py-2">
                      <SeverityBadge severity={c.severity} />
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="border-b border-line/70 bg-panel-2/40">
                      <td />
                      <td colSpan={6} className="px-3 py-3">
                        <p className="text-[12.5px] text-fg-2">
                          <span className="font-medium text-fg">Evidence:</span> {c.evidence}
                        </p>
                        <p className="mt-1 text-[12px] text-fg-3">
                          category {c.category} · id <span className="font-mono">{c.id}</span>
                        </p>
                        <div className="mt-2 grid gap-2 md:grid-cols-2">
                          <ValueBlock label="Old value" value={pretty(c.old_value)} />
                          <ValueBlock label="New value" value={pretty(c.new_value)} />
                        </div>
                        {Object.keys(c.details).length > 0 && (
                          <ValueBlock label="Details" value={pretty(c.details)} />
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="px-4 py-6 text-center text-sm text-fg-3">No changes match the filters.</p>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[12px] text-fg-3">
        <span>
          Showing {Math.min(limit, filtered.length).toLocaleString()} of {filtered.length.toLocaleString()}{' '}
          listed
          {total > changes.length &&
            ` · ${(total - changes.length).toLocaleString()} more counted but not listed (see limits)`}
        </span>
        {filtered.length > limit && (
          <Button size="sm" variant="secondary" onClick={() => setLimit((l) => l + PAGE)}>
            Show more
          </Button>
        )}
      </div>
    </div>
  )
}

function ValueBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-1 min-w-0">
      <p className="mb-1 text-[11.5px] font-medium text-fg-3">{label}</p>
      <pre className="max-h-64 overflow-auto rounded-md border border-line bg-bg p-2 font-mono text-[12px] whitespace-pre-wrap break-all">
        {value}
      </pre>
    </div>
  )
}
