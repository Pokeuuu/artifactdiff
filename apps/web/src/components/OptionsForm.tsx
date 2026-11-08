import { Input, Label, Select } from '@/components/ui/input'
import type { JsonSchemaProp, PluginMetadata } from '@/lib/types'

/** Resolve `anyOf: [{type: X}, {type: 'null'}]` into a base type plus nullability. */
function baseType(p: JsonSchemaProp): { type: string; nullable: boolean; enumValues?: string[] } {
  if (p.enum) return { type: 'enum', nullable: false, enumValues: p.enum.map(String) }
  if (p.anyOf) {
    const nonNull = p.anyOf.filter((x) => x.type !== 'null')
    const inner = nonNull[0] ? baseType(nonNull[0]) : { type: 'string', nullable: true }
    return { ...inner, nullable: true }
  }
  return { type: p.type ?? 'string', nullable: false }
}

interface Props {
  plugin: PluginMetadata
  values: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
}

/** A form generated from the plugin's options JSON schema. Untouched fields use the plugin defaults. */
export function OptionsForm({ plugin, values, onChange }: Props) {
  const props = plugin.options_schema.properties ?? {}
  const entries = Object.entries(props)
  if (!entries.length) return <p className="text-sm text-fg-3">This plugin has no options.</p>

  const set = (key: string, v: unknown) => {
    const next = { ...values }
    if (v === undefined) delete next[key]
    else next[key] = v
    onChange(next)
  }

  return (
    <div className="grid gap-x-5 gap-y-4 sm:grid-cols-2">
      {entries.map(([key, spec]) => {
        const t = baseType(spec)
        const id = `opt-${key}`
        const current = values[key]
        const def = spec.default
        const help = spec.description
        let control
        if (t.type === 'boolean') {
          const checked = current === undefined ? Boolean(def) : Boolean(current)
          control = (
            <label className="flex h-9 cursor-pointer items-center gap-2 text-sm">
              <input
                id={id}
                type="checkbox"
                checked={checked}
                onChange={(e) => set(key, e.target.checked === Boolean(def) ? undefined : e.target.checked)}
                className="size-4 accent-[var(--accent)]"
              />
              {checked ? 'Enabled' : 'Disabled'}
            </label>
          )
        } else if (t.type === 'enum') {
          control = (
            <Select
              id={id}
              value={String(current ?? def ?? '')}
              onChange={(e) => set(key, e.target.value === String(def) ? undefined : e.target.value)}
            >
              {t.enumValues?.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </Select>
          )
        } else if (t.type === 'array') {
          control = (
            <Input
              id={id}
              placeholder={Array.isArray(def) && def.length ? def.join(', ') : 'comma-separated'}
              value={Array.isArray(current) ? current.join(', ') : ''}
              onChange={(e) => {
                const parts = e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean)
                set(key, e.target.value.trim() ? parts : undefined)
              }}
            />
          )
        } else if (t.type === 'integer' || t.type === 'number') {
          control = (
            <Input
              id={id}
              type="number"
              step={t.type === 'integer' ? 1 : 'any'}
              placeholder={def === null || def === undefined ? '' : String(def)}
              value={current === undefined ? '' : String(current)}
              onChange={(e) => set(key, e.target.value === '' ? undefined : Number(e.target.value))}
            />
          )
        } else {
          control = (
            <Input
              id={id}
              placeholder={def === null || def === undefined ? (t.nullable ? 'unset' : '') : String(def)}
              value={typeof current === 'string' ? current : ''}
              onChange={(e) => set(key, e.target.value === '' ? undefined : e.target.value)}
            />
          )
        }
        return (
          <div key={key} className="min-w-0">
            <Label htmlFor={id} className="font-mono">
              {key}
            </Label>
            <div className="mt-1">{control}</div>
            {help && <p className="mt-1 text-[12px] leading-snug text-fg-3">{help}</p>}
          </div>
        )
      })}
    </div>
  )
}
