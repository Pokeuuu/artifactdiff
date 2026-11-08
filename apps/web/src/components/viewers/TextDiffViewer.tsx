// Monaco diff editor. Monaco (and its web workers) are bundled locally: by default @monaco-editor/react
// would load Monaco from a CDN, which would break the local-first/no-network guarantee.
import { DiffEditor, loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { useMemo, useState } from 'react'
import { ErrorState } from '@/components/common'
import { Skeleton } from '@/components/ui/skeleton'
import { useFetched } from '@/lib/hooks'
import { useSettings } from '@/lib/store'
import type { ComparisonDetail } from '@/lib/types'

loader.config({ monaco })

const MAX_BYTES = 5 * 1024 * 1024
const LANG: Record<string, string> = {
  '.json': 'json',
  '.yaml': 'yaml',
  '.yml': 'yaml',
  '.md': 'markdown',
  '.xml': 'xml',
  '.html': 'html',
  '.css': 'css',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.py': 'python',
  '.sql': 'sql',
  '.ini': 'ini',
  '.toml': 'ini',
  '.sh': 'shell',
  '.csv': 'plaintext',
}

function sortKeys(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortKeys)
  if (v && typeof v === 'object')
    return Object.fromEntries(
      Object.keys(v)
        .sort()
        .map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]),
    )
  return v
}

function normalizeJson(text: string): string {
  try {
    return JSON.stringify(sortKeys(JSON.parse(text)), null, 2)
  } catch {
    return text
  }
}

export default function TextDiffViewer({ detail }: { detail: ComparisonDetail }) {
  const tooBig = detail.old_size > MAX_BYTES || detail.new_size > MAX_BYTES
  const base = `/api/v1/comparisons/${detail.id}/files`
  const enabled = detail.inputs_available && !tooBig
  const a = useFetched(enabled ? `${base}/old` : null, 'text')
  const b = useFetched(enabled ? `${base}/new` : null, 'text')
  const isJson = detail.plugin === 'json'
  const [normalize, setNormalize] = useState(isJson)
  const theme = useSettings((s) => s.theme)
  const dark = theme !== 'light' && document.documentElement.classList.contains('dark')
  const ext = detail.new_name.slice(detail.new_name.lastIndexOf('.')).toLowerCase()
  const [left, right] = useMemo(() => {
    if (a.data === null || b.data === null) return [null, null]
    return normalize ? [normalizeJson(a.data), normalizeJson(b.data)] : [a.data, b.data]
  }, [a.data, b.data, normalize])

  if (!detail.inputs_available)
    return (
      <p className="text-sm text-fg-3">
        Source view unavailable: uploaded files were deleted by the retention policy.
      </p>
    )
  if (tooBig) return <p className="text-sm text-fg-3">Source view is disabled for files over 5 MiB.</p>
  if (a.error || b.error) return <ErrorState error={a.error ?? b.error} />
  return (
    <div>
      {isJson && (
        <label className="mb-2 flex w-fit cursor-pointer items-center gap-2 text-[12.5px] text-fg-2">
          <input
            type="checkbox"
            checked={normalize}
            onChange={(e) => setNormalize(e.target.checked)}
            className="accent-[var(--accent)]"
          />
          Normalize JSON (sort keys, consistent indentation)
        </label>
      )}
      {left === null || right === null ? (
        <Skeleton className="h-[420px]" />
      ) : (
        <div className="overflow-hidden rounded-lg border border-line" data-testid="monaco-diff">
          <DiffEditor
            height="460px"
            original={left}
            modified={right}
            language={LANG[ext] ?? 'plaintext'}
            theme={dark ? 'vs-dark' : 'light'}
            options={{
              readOnly: true,
              renderSideBySide: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              fontSize: 12.5,
              originalEditable: false,
              automaticLayout: true,
            }}
          />
        </div>
      )}
    </div>
  )
}
