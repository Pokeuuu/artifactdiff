// Typed client for the ArtifactDiff REST API. All requests go to the same origin (proxied in dev).
import { useSettings } from './store'
import type { ComparisonDetail, ComparisonPage, Health, PluginMetadata, ServerConfig, Stats } from './types'

export const API = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function authHeaders(): HeadersInit {
  const token = useSettings.getState().apiToken
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path.startsWith('/') ? path : `${API}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d?.msg ?? String(d)).join('; ')
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export interface ListParams {
  q?: string
  plugin?: string
  status?: string
  sort?: string
  limit?: number
  offset?: number
}

export const api = {
  health: () => request<Health>(`${API}/health`),
  config: () => request<ServerConfig>(`${API}/config`),
  stats: () => request<Stats>(`${API}/stats`),
  plugins: () => request<{ plugins: PluginMetadata[] }>(`${API}/plugins`).then((r) => r.plugins),
  comparisons: (p: ListParams = {}) => {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== '') qs.set(k, String(v))
    return request<ComparisonPage>(`${API}/comparisons?${qs}`)
  },
  comparison: (id: string) => request<ComparisonDetail>(`${API}/comparisons/${id}`),
  cancel: (id: string) => request(`${API}/comparisons/${id}/cancel`, { method: 'POST' }),
  remove: (id: string) => request<void>(`${API}/comparisons/${id}`, { method: 'DELETE' }),
  clearHistory: () => request<void>(`${API}/comparisons?confirm=true`, { method: 'DELETE' }),
  compare: (old: File, neu: File, plugin: string | null, options: Record<string, unknown>) => {
    const form = new FormData()
    form.append('old', old, old.name)
    form.append('new', neu, neu.name)
    if (plugin) form.append('plugin', plugin)
    if (Object.keys(options).length) form.append('options', JSON.stringify(options))
    return request<{ id: string; job_status: string; url: string }>(`${API}/compare`, {
      method: 'POST',
      body: form,
    })
  },
}

/** Fetch a protected binary resource (asset, input file, report) with auth headers. */
export async function fetchBlob(url: string): Promise<Blob> {
  const res = await fetch(url, { headers: authHeaders() })
  if (!res.ok)
    throw new ApiError(res.status, res.status === 410 ? 'Input files are no longer retained' : res.statusText)
  return res.blob()
}

export function reportUrl(id: string, format: 'html' | 'markdown' | 'json'): string {
  return `${API}/comparisons/${id}/report?format=${format}`
}

export async function downloadReport(id: string, format: 'html' | 'markdown' | 'json'): Promise<void> {
  const blob = await fetchBlob(reportUrl(id, format))
  const ext = format === 'markdown' ? 'md' : format
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `artifactdiff-${id.slice(0, 8)}.${ext}`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 1000)
}
