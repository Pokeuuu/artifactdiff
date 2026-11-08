import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { useState } from 'react'
import { ErrorState, KeyValue, PageHeader } from '@/components/common'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/dialog'
import { Input, Label } from '@/components/ui/input'
import { api } from '@/lib/api'
import { type Theme, useSettings } from '@/lib/store'
import { cn, formatBytes } from '@/lib/utils'

export function SettingsPage() {
  const qc = useQueryClient()
  const { theme, setTheme, apiToken, setApiToken } = useSettings()
  const [token, setToken] = useState(apiToken)
  const [confirm, setConfirm] = useState(false)
  const config = useQuery({ queryKey: ['config'], queryFn: api.config })
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const clear = useMutation({
    mutationFn: api.clearHistory,
    onSuccess: () => {
      setConfirm(false)
      qc.invalidateQueries()
    },
  })

  return (
    <div className="animate-fade-in max-w-3xl">
      <PageHeader
        title="Settings"
        description="Preferences are stored in this browser. Server limits are set by the operator."
      />
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
          </CardHeader>
          <CardContent>
            <div
              role="radiogroup"
              aria-label="Theme"
              className="inline-flex rounded-md border border-line bg-panel-2 p-0.5"
            >
              {(['dark', 'light', 'system'] as Theme[]).map((t) => (
                <button
                  key={t}
                  role="radio"
                  aria-checked={theme === t}
                  onClick={() => setTheme(t)}
                  className={cn(
                    'h-7 cursor-pointer rounded px-3 text-[13px] capitalize',
                    theme === t ? 'bg-panel text-fg shadow-sm' : 'text-fg-3 hover:text-fg',
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>API access</CardTitle>
          </CardHeader>
          <CardContent>
            <Label htmlFor="token">API token</Label>
            <div className="mt-1 flex gap-2">
              <Input
                id="token"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={
                  config.data?.auth_required ? 'Required by this server' : 'Not required by this server'
                }
              />
              <Button
                variant="secondary"
                onClick={() => {
                  setApiToken(token.trim())
                  qc.invalidateQueries()
                }}
              >
                Save
              </Button>
            </div>
            <p className="mt-1.5 text-[12px] text-fg-3">
              Only needed when the server sets ARTIFACTDIFF_API_TOKEN. Stored in this browser's local storage.
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Server</CardTitle>
          </CardHeader>
          <CardContent>
            {config.isError && <ErrorState error={config.error} />}
            {config.data && (
              <KeyValue
                data={{
                  version: config.data.version,
                  'upload limit': `${config.data.max_upload_mb} MB per file`,
                  'upload retention': `${config.data.input_retention_hours} hours`,
                  'result retention': config.data.result_retention_days
                    ? `${config.data.result_retention_days} days`
                    : 'kept until deleted',
                  'time limit': `${config.data.timeout_seconds} s per comparison`,
                  isolation: config.data.isolation === 'process' ? 'separate worker process' : 'in-process',
                  'stored uploads': stats.data ? formatBytes(stats.data.storage.inputs) : '…',
                  'stored visualizations': stats.data ? formatBytes(stats.data.storage.assets) : '…',
                }}
              />
            )}
            <p className="mt-3 text-[12px] text-fg-3">
              ArtifactDiff sends no telemetry. Files are analysed on this server only.
            </p>
          </CardContent>
        </Card>
        <Card className="border-remove/30">
          <CardHeader>
            <CardTitle>Danger zone</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-fg-2">Delete every comparison, visualization and retained upload.</p>
            <Button variant="danger" onClick={() => setConfirm(true)}>
              <Trash2 /> Clear history
            </Button>
          </CardContent>
        </Card>
      </div>
      {clear.isError && <ErrorState error={clear.error} className="mt-3" />}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Clear the entire history?"
        description="This cannot be undone."
        confirmLabel="Clear history"
        onConfirm={() => clear.mutate()}
        busy={clear.isPending}
      />
    </div>
  )
}
