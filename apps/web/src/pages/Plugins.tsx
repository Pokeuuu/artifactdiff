import { useQuery } from '@tanstack/react-query'
import { CircleAlert, CircleCheck } from 'lucide-react'
import { ErrorState, PageHeader } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'

const DOCS = 'https://github.com/Pokeuuu/artifactdiff/blob/main/docs/supported-formats.md'

export function PluginsPage() {
  const q = useQuery({ queryKey: ['plugins'], queryFn: api.plugins })
  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Plugins"
        description="Plugins registered on this server. Third-party plugins load only when explicitly enabled in the server configuration."
      />
      {q.isError && <ErrorState error={q.error} />}
      {!q.data && !q.isError && <Skeleton className="h-64" />}
      <div className="grid gap-4 md:grid-cols-2">
        {q.data?.map((p) => (
          <Card key={p.name} data-testid={`plugin-${p.name}`}>
            <CardContent className="pt-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="font-semibold">{p.display_name}</h3>
                  <p className="font-mono text-[12px] text-fg-3">
                    {p.name} {p.version} · plugin API {p.api_version} · {p.builtin ? 'built-in' : 'external'}
                  </p>
                </div>
                {p.available ? (
                  <Badge tone="add">
                    <CircleCheck /> Available
                  </Badge>
                ) : (
                  <Badge tone="modify">
                    <CircleAlert /> Missing dependencies
                  </Badge>
                )}
              </div>
              <p className="mt-2 text-sm text-fg-2">{p.description}</p>
              {!p.available && p.unavailable_reason && (
                <p className="mt-2 rounded-md bg-panel-2 px-2 py-1.5 font-mono text-[12px] text-modify">
                  {p.unavailable_reason}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-1">
                {(p.formats[0]?.extensions.length ? p.formats[0].extensions : ['any file']).map((e) => (
                  <Badge key={e} className="font-mono">
                    {e}
                  </Badge>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {p.capabilities.map((c) => (
                  <Badge key={c} tone="info">
                    {c}
                  </Badge>
                ))}
              </div>
              {p.limitations.length > 0 && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-[12.5px] text-fg-2">
                    Limitations ({p.limitations.length})
                  </summary>
                  <ul className="mt-1.5 list-disc space-y-1 pl-5 text-[12.5px] text-fg-2">
                    {p.limitations.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </details>
              )}
              <p className="mt-3 text-[12px] text-fg-3">
                {Object.keys(p.options_schema.properties ?? {}).length} option(s) ·{' '}
                <a
                  href={p.documentation_url ?? `${DOCS}#${p.name}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline"
                >
                  Documentation
                </a>
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  )
}
