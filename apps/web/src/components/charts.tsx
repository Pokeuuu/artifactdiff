// Single-series charts (one hue: --series-1, stepped per theme). Thin rounded bars, 2px gaps,
// recessive grid, hover tooltip; every chart also exposes its data as a visually hidden table.
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

interface TooltipProps {
  active?: boolean
  payload?: { value: number; payload: Record<string, unknown> }[]
  label?: string | number
  unit: string
  labelKey?: string
}

function ChartTooltip({ active, payload, label, unit, labelKey }: TooltipProps) {
  if (!active || !payload?.length) return null
  const p = payload[0]!
  const name = labelKey ? String(p.payload[labelKey]) : String(label)
  return (
    <div className="rounded-md border border-line bg-panel px-2.5 py-1.5 text-[12px] shadow-lg">
      <p className="text-fg-3">{name}</p>
      <p className="font-medium text-fg tabular-nums">
        {p.value.toLocaleString()} {unit}
      </p>
    </div>
  )
}

const axis = { stroke: 'var(--grid)', tick: { fill: 'var(--fg-3)', fontSize: 11 }, tickLine: false }

function DataTable({ caption, rows }: { caption: string; rows: [string, number][] }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <th scope="row">{k}</th>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ActivityChart({ data }: { data: { date: string; comparisons: number }[] }) {
  const rows = data.map((d) => ({ ...d, label: d.date.slice(5) }))
  return (
    <figure aria-label="Comparisons per day, last 30 days">
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 4, bottom: 0, left: -24 }} barCategoryGap={2}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="label" {...axis} interval={4} axisLine={false} />
            <YAxis allowDecimals={false} {...axis} axisLine={false} width={48} />
            <Tooltip
              cursor={{ fill: 'var(--accent-soft)' }}
              content={<ChartTooltip unit="comparisons" labelKey="date" />}
            />
            <Bar
              dataKey="comparisons"
              fill="var(--series-1)"
              radius={[4, 4, 0, 0]}
              maxBarSize={18}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable caption="Comparisons per day" rows={data.map((d) => [d.date, d.comparisons])} />
    </figure>
  )
}

export function HorizontalBars({
  data,
  unit,
  caption,
  format = (v: number) => v.toLocaleString(),
}: {
  data: { name: string; value: number }[]
  unit: string
  caption: string
  format?: (v: number) => string
}) {
  const height = Math.max(80, data.length * 30 + 16)
  return (
    <figure aria-label={caption}>
      <div style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 48, bottom: 0, left: 0 }}
            barCategoryGap={8}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={130}
              axisLine={false}
              tickLine={false}
              tick={{ fill: 'var(--fg-2)', fontSize: 12, fontFamily: 'var(--font-mono)' }}
            />
            <Tooltip
              cursor={{ fill: 'var(--accent-soft)' }}
              content={<ChartTooltip unit={unit} labelKey="name" />}
            />
            <Bar
              dataKey="value"
              fill="var(--series-1)"
              radius={[0, 4, 4, 0]}
              maxBarSize={14}
              isAnimationActive={false}
              label={{
                position: 'right',
                fill: 'var(--fg-2)',
                fontSize: 11,
                formatter: (v: unknown) => format(Number(v)),
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <DataTable caption={caption} rows={data.map((d) => [d.name, d.value])} />
    </figure>
  )
}
