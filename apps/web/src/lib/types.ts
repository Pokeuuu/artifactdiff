// Types mirroring the API / report JSON schema (docs/schema/result.schema.json).

export type ChangeKind = 'added' | 'removed' | 'modified' | 'type_changed' | 'renamed' | 'moved'
export type Severity = 'info' | 'low' | 'medium' | 'high'
export type ResultStatus = 'identical' | 'equivalent' | 'different' | 'error' | 'unsupported'
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

export interface Change {
  id: string
  kind: ChangeKind
  category: string
  severity: Severity
  path: string
  description: string
  old_value: Json | null
  new_value: Json | null
  confidence: number | null
  evidence: string
  details: Record<string, Json>
}

export interface ArtifactFormat {
  id: string
  name: string
  extensions: string[]
  mime_types: string[]
}

export interface ArtifactMetadata {
  name: string
  size_bytes: number
  sha256: string
  format: ArtifactFormat | null
  detected_by: string | null
  properties: Record<string, Json>
}

export interface Asset {
  id: string
  label: string
  role: string
  media_type: string
  width: number | null
  height: number | null
  url: string | null
  data_base64: string | null
  size_bytes: number
}

export interface ComparisonResult {
  schema_version: string
  artifactdiff_version: string
  created_at: string
  status: ResultStatus
  plugin: { name: string; version: string } | null
  old: ArtifactMetadata
  new: ArtifactMetadata
  options: Record<string, Json>
  summary: string
  changes: Change[]
  warnings: { code: string; message: string; path: string | null }[]
  limitations: string[]
  statistics: {
    total_changes: number
    reported_changes: number
    by_kind: Record<string, number>
    by_category: Record<string, number>
    by_severity: Record<string, number>
    max_severity: Severity | null
    metrics: Record<string, Json>
    duration_ms: number
  }
  assets: Asset[]
  error: string | null
  reproducibility: Record<string, Json> | null
  fingerprint: string
}

export interface ComparisonSummary {
  id: string
  created_at: string
  finished_at: string | null
  job_status: JobStatus
  result_status: ResultStatus | null
  plugin: string | null
  plugin_version: string | null
  old_name: string
  new_name: string
  old_size: number
  new_size: number
  total_changes: number
  max_severity: Severity | null
  summary: string | null
  error: string | null
  duration_ms: number | null
  inputs_available: boolean
  progress: number | null
  progress_message: string | null
}

export interface ComparisonDetail extends ComparisonSummary {
  options: Record<string, Json>
  result: ComparisonResult | null
}

export interface ComparisonPage {
  items: ComparisonSummary[]
  total: number
  limit: number
  offset: number
}

export interface JsonSchemaProp {
  type?: string
  enum?: Json[]
  anyOf?: JsonSchemaProp[]
  items?: JsonSchemaProp
  default?: Json
  description?: string
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  title?: string
}

export interface PluginMetadata {
  name: string
  version: string
  display_name: string
  description: string
  api_version: string
  formats: ArtifactFormat[]
  capabilities: string[]
  limitations: string[]
  requires: string[]
  available: boolean
  unavailable_reason: string | null
  builtin: boolean
  options_schema: { properties?: Record<string, JsonSchemaProp> }
  documentation_url: string | null
}

export interface ServerConfig {
  max_upload_mb: number
  input_retention_hours: number
  result_retention_days: number
  timeout_seconds: number
  isolation: string
  auth_required: boolean
  version: string
}

export interface Health {
  status: 'ok'
  version: string
  plugins_available: number
  plugins_total: number
  database: 'ok' | 'error'
}

export interface Stats {
  total_comparisons: number
  completed: number
  failed: number
  files_analyzed: number
  bytes_analyzed: number
  total_changes: number
  by_result_status: Record<string, number>
  plugin_usage: { plugin: string; comparisons: number }[]
  activity: { date: string; comparisons: number; changes: number }[]
  recent_changes: {
    comparison_id: string
    file: string
    path: string
    kind: ChangeKind
    severity: Severity
    description: string
  }[]
  supported_formats: number
  supported_extensions: string[]
  storage: { inputs: number; assets: number; total: number }
}
