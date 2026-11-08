import {
  AlertTriangle,
  CircleCheck,
  CircleDashed,
  CircleX,
  Equal,
  Info,
  Loader2,
  Minus,
  type LucideIcon,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import type { ChangeKind, JobStatus, ResultStatus, Severity } from '@/lib/types'
import { cn } from '@/lib/utils'

const SEVERITY: Record<Severity, { tone: 'info' | 'add' | 'modify' | 'remove'; label: string }> = {
  info: { tone: 'info', label: 'info' },
  low: { tone: 'add', label: 'low' },
  medium: { tone: 'modify', label: 'medium' },
  high: { tone: 'remove', label: 'high' },
}

export function SeverityBadge({ severity }: { severity: Severity | null }) {
  if (!severity) return <span className="text-fg-3">—</span>
  const s = SEVERITY[severity]
  return (
    <Badge tone={s.tone} title={`Severity: ${s.label} (plugin heuristic)`}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {s.label}
    </Badge>
  )
}

const KIND_TONE: Record<ChangeKind, 'add' | 'remove' | 'modify' | 'accent' | 'neutral'> = {
  added: 'add',
  removed: 'remove',
  modified: 'modify',
  type_changed: 'modify',
  renamed: 'accent',
  moved: 'neutral',
}

export function KindBadge({ kind }: { kind: ChangeKind }) {
  const sign = kind === 'added' ? '+' : kind === 'removed' ? '−' : kind === 'renamed' ? '→' : '~'
  return (
    <Badge tone={KIND_TONE[kind]} className="font-mono">
      <span aria-hidden>{sign}</span>
      {kind.replace('_', ' ')}
    </Badge>
  )
}

const RESULT: Record<
  ResultStatus,
  { icon: LucideIcon; tone: 'neutral' | 'modify' | 'remove' | 'info'; label: string }
> = {
  identical: { icon: Equal, tone: 'info', label: 'Identical' },
  equivalent: { icon: CircleCheck, tone: 'info', label: 'Equivalent' },
  different: { icon: AlertTriangle, tone: 'modify', label: 'Different' },
  error: { icon: CircleX, tone: 'remove', label: 'Error' },
  unsupported: { icon: Minus, tone: 'neutral', label: 'Unsupported' },
}

export function StatusBadge({ job, result }: { job: JobStatus; result: ResultStatus | null }) {
  if (job === 'queued' || job === 'running')
    return (
      <Badge tone="accent">
        {job === 'running' ? <Loader2 className="animate-spin" /> : <CircleDashed />}
        {job === 'running' ? 'Running' : 'Queued'}
      </Badge>
    )
  if (job === 'failed')
    return (
      <Badge tone="remove">
        <CircleX /> Failed
      </Badge>
    )
  if (job === 'cancelled') return <Badge tone="neutral">Cancelled</Badge>
  if (!result) return <Badge>—</Badge>
  const r = RESULT[result]
  const Icon = r.icon
  return (
    <Badge tone={r.tone}>
      <Icon /> {r.label}
    </Badge>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-fg-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon
  title: string
  children?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-line px-6 py-12 text-center">
      <div className="mb-3 grid size-10 place-items-center rounded-full bg-panel-2 text-fg-3">
        <Icon className="size-5" />
      </div>
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-fg-2">{children}</div>}
    </div>
  )
}

export function ErrorState({ error, className }: { error: unknown; className?: string }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-2 rounded-md border border-remove/30 bg-remove/10 px-3 py-2 text-sm text-remove',
        className,
      )}
    >
      <CircleX className="mt-0.5 size-4 shrink-0" />
      <span className="break-words">{msg}</span>
    </div>
  )
}

export function Callout({
  tone,
  title,
  children,
}: {
  tone: 'warn' | 'info'
  title?: string
  children: ReactNode
}) {
  const Icon = tone === 'warn' ? AlertTriangle : Info
  return (
    <div
      className={cn(
        'flex gap-2.5 rounded-md border px-3 py-2.5 text-sm',
        tone === 'warn' ? 'border-modify/30 bg-modify/8 text-fg' : 'border-line bg-panel-2 text-fg-2',
      )}
    >
      <Icon className={cn('mt-0.5 size-4 shrink-0', tone === 'warn' ? 'text-modify' : 'text-fg-3')} />
      <div className="min-w-0">
        {title && <p className="font-medium text-fg">{title}</p>}
        {children}
      </div>
    </div>
  )
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <p className="text-[12px] font-medium text-fg-3">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums tracking-tight">{value}</p>
      {hint && <p className="mt-0.5 text-[12px] text-fg-3">{hint}</p>}
    </div>
  )
}

export function KeyValue({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data)
  if (!entries.length) return <p className="text-sm text-fg-3">No properties.</p>
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-[13px]">
      {entries.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="truncate text-fg-3" title={k}>
            {k}
          </dt>
          <dd
            className="min-w-0 truncate font-mono text-[12.5px] text-fg"
            title={typeof v === 'string' ? v : JSON.stringify(v)}
          >
            {v === null || v === undefined ? '—' : typeof v === 'string' ? v : JSON.stringify(v)}
          </dd>
        </div>
      ))}
    </dl>
  )
}
