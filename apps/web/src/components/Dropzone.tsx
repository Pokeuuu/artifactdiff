import { FileUp, X } from 'lucide-react'
import { useId, useRef, useState, type DragEvent } from 'react'
import { cn, formatBytes } from '@/lib/utils'

interface Props {
  label: string
  file: File | null
  onFile: (f: File | null) => void
  error?: string | null
  testId?: string
}

export function Dropzone({ label, file, onFile, error, testId }: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const id = useId()

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setOver(false)
    const f = e.dataTransfer.files?.[0]
    if (f) onFile(f)
  }

  return (
    <div className="min-w-0">
      <div className="mb-1.5 flex items-center justify-between">
        <span id={`${id}-label`} className="text-[12.5px] font-medium text-fg-2">
          {label}
        </span>
        {file && (
          <button
            type="button"
            onClick={() => onFile(null)}
            className="inline-flex cursor-pointer items-center gap-1 text-[12px] text-fg-3 hover:text-fg"
          >
            <X className="size-3" /> Clear
          </button>
        )}
      </div>
      <div
        role="button"
        tabIndex={0}
        aria-labelledby={`${id}-label`}
        data-testid={testId}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) =>
          (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), inputRef.current?.click())
        }
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        className={cn(
          'flex h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 text-center transition-colors',
          over ? 'border-accent bg-accent-soft' : 'border-line-strong bg-panel hover:border-fg-3',
          error && 'border-remove/60',
        )}
      >
        {file ? (
          <>
            <p className="max-w-full truncate font-mono text-[13px] text-fg" title={file.name}>
              {file.name}
            </p>
            <p className="text-[12px] text-fg-3">{formatBytes(file.size)}</p>
          </>
        ) : (
          <>
            <FileUp className="size-5 text-fg-3" />
            <p className="text-sm text-fg-2">
              Drop a file or <span className="text-accent">browse</span>
            </p>
          </>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        data-testid={testId ? `${testId}-input` : undefined}
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
      />
      {error && <p className="mt-1.5 text-[12px] text-remove">{error}</p>}
    </div>
  )
}
