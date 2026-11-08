import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowLeftRight, GitCompareArrows, Loader2, ShieldCheck } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Callout, ErrorState, PageHeader } from '@/components/common'
import { Dropzone } from '@/components/Dropzone'
import { OptionsForm } from '@/components/OptionsForm'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label, Select } from '@/components/ui/input'
import { api } from '@/lib/api'
import type { PluginMetadata } from '@/lib/types'

function ext(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(i).toLowerCase() : ''
}

/** Best-effort client-side preview of which plugin the server will pick (the server decides). */
function guessPlugin(plugins: PluginMetadata[], a: File | null, b: File | null): PluginMetadata | null {
  if (!a || !b) return null
  const matches = (p: PluginMetadata, f: File) =>
    p.formats.some((fmt) => fmt.extensions.includes(ext(f.name)))
  return (
    plugins.find(
      (p) => p.available && !['text', 'binary'].includes(p.name) && matches(p, a) && matches(p, b),
    ) ?? null
  )
}

export function ComparePage() {
  const navigate = useNavigate()
  const config = useQuery({ queryKey: ['config'], queryFn: api.config })
  const plugins = useQuery({ queryKey: ['plugins'], queryFn: api.plugins })
  const [oldFile, setOld] = useState<File | null>(null)
  const [newFile, setNew] = useState<File | null>(null)
  const [plugin, setPlugin] = useState('')
  const [options, setOptions] = useState<Record<string, unknown>>({})

  const maxBytes = (config.data?.max_upload_mb ?? Infinity) * 1024 * 1024
  const tooBig = (f: File | null) =>
    f && f.size > maxBytes ? `Larger than the server limit of ${config.data?.max_upload_mb} MB` : null
  const available = useMemo(() => (plugins.data ?? []).filter((p) => p.available), [plugins.data])
  const guessed = guessPlugin(available, oldFile, newFile)
  const effective = plugin ? (available.find((p) => p.name === plugin) ?? null) : guessed

  const submit = useMutation({
    mutationFn: () => api.compare(oldFile!, newFile!, plugin || null, options),
    onSuccess: (r) => navigate(`/comparisons/${r.id}`),
  })

  const errors = [tooBig(oldFile), tooBig(newFile)]
  const sameFile =
    oldFile &&
    newFile &&
    oldFile.name === newFile.name &&
    oldFile.size === newFile.size &&
    oldFile.lastModified === newFile.lastModified
  const canSubmit = !!oldFile && !!newFile && !errors[0] && !errors[1] && !submit.isPending

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Compare"
        description="Upload two versions of an artifact. Nothing leaves this server."
      />
      <Card>
        <CardContent className="pt-4">
          <div className="grid items-center gap-4 md:grid-cols-[1fr_auto_1fr]">
            <Dropzone
              label="Old version"
              file={oldFile}
              onFile={setOld}
              error={errors[0]}
              testId="drop-old"
            />
            <button
              type="button"
              onClick={() => {
                setOld(newFile)
                setNew(oldFile)
              }}
              disabled={!oldFile && !newFile}
              className="mx-auto mt-6 grid size-8 cursor-pointer place-items-center rounded-full border border-line text-fg-3 hover:text-fg disabled:opacity-40"
              aria-label="Swap old and new"
              title="Swap"
            >
              <ArrowLeftRight className="size-4" />
            </button>
            <Dropzone
              label="New version"
              file={newFile}
              onFile={setNew}
              error={errors[1]}
              testId="drop-new"
            />
          </div>
          {sameFile && (
            <div className="mt-4">
              <Callout tone="warn">The same file was selected twice; the result will be “identical”.</Callout>
            </div>
          )}
          <div className="mt-5 grid gap-4 md:grid-cols-[280px_1fr]">
            <div>
              <Label htmlFor="plugin">Plugin</Label>
              <Select
                id="plugin"
                className="mt-1"
                value={plugin}
                onChange={(e) => {
                  setPlugin(e.target.value)
                  setOptions({})
                }}
              >
                <option value="">Auto-detect{guessed ? ` (${guessed.display_name})` : ''}</option>
                {available.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.display_name}
                  </option>
                ))}
              </Select>
              <p className="mt-1.5 text-[12px] text-fg-3">
                Detection uses file signatures, MIME type and extension. Formats without a plugin fall back to
                text or byte-level comparison.
              </p>
            </div>
            <div className="flex items-end justify-end gap-3">
              {config.data && (
                <p className="mr-auto hidden items-center gap-1.5 text-[12px] text-fg-3 md:flex">
                  <ShieldCheck className="size-4" />
                  Uploads are deleted after {config.data.input_retention_hours} h · limit{' '}
                  {config.data.max_upload_mb} MB per file
                </p>
              )}
              <Button
                size="lg"
                disabled={!canSubmit}
                onClick={() => submit.mutate()}
                data-testid="start-compare"
              >
                {submit.isPending ? <Loader2 className="animate-spin" /> : <GitCompareArrows />}
                Compare
              </Button>
            </div>
          </div>
          {submit.isError && <ErrorState error={submit.error} className="mt-4" />}
        </CardContent>
      </Card>

      {effective && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Options · {effective.display_name}</CardTitle>
            {Object.keys(options).length > 0 && (
              <button
                className="cursor-pointer text-[12.5px] text-fg-3 hover:text-fg"
                onClick={() => setOptions({})}
              >
                Reset to defaults
              </button>
            )}
          </CardHeader>
          <CardContent>
            {!plugin && (
              <p className="mb-3 text-[12px] text-fg-3">
                Shown for the plugin the server is expected to select; choose it explicitly to make sure these
                apply.
              </p>
            )}
            <OptionsForm plugin={effective} values={options} onChange={setOptions} />
          </CardContent>
        </Card>
      )}
      {plugins.isError && <ErrorState error={plugins.error} className="mt-4" />}
    </div>
  )
}
