import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  GitCompareArrows,
  History,
  LayoutDashboard,
  Menu,
  Moon,
  Puzzle,
  Settings,
  Sun,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '@/lib/api'
import { applyTheme, useSettings } from '@/lib/store'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/compare', label: 'Compare', icon: GitCompareArrows },
  { to: '/history', label: 'History', icon: History },
  { to: '/plugins', label: 'Plugins', icon: Puzzle },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/docs', label: 'Documentation', icon: BookOpen },
]

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-6" aria-hidden>
      <path
        d="M9 9h8a6 6 0 0 1 0 12h-3"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <path
        d="M23 23h-8a6 6 0 0 1 0-12h3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        opacity=".5"
      />
    </svg>
  )
}

export function Layout() {
  const theme = useSettings((s) => s.theme)
  const setTheme = useSettings((s) => s.setTheme)
  const [mobileOpen, setMobileOpen] = useState(false)
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 30_000, retry: 1 })

  useEffect(() => {
    applyTheme(theme)
    if (theme !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => applyTheme('system')
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [theme])

  const isDark =
    theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)

  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col gap-1 p-3">
      <div className="mb-4 flex items-center gap-2 px-2 pt-1">
        <Logo />
        <span className="font-semibold tracking-tight">ArtifactDiff</span>
      </div>
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={() => setMobileOpen(false)}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13.5px] font-medium transition-colors',
              isActive ? 'bg-panel-2 text-fg' : 'text-fg-2 hover:bg-panel-2/60 hover:text-fg',
            )
          }
        >
          <Icon className="size-4" />
          {label}
        </NavLink>
      ))}
      <div className="mt-auto space-y-2 px-2 pb-1 text-[12px] text-fg-3">
        <div className="flex items-center gap-2" title="API status">
          <span
            className={cn(
              'size-2 rounded-full',
              health.isSuccess ? 'bg-add' : health.isError ? 'bg-remove' : 'bg-fg-3',
            )}
          />
          {health.isSuccess
            ? `Local API · v${health.data.version}`
            : health.isError
              ? 'API unreachable'
              : 'Connecting…'}
        </div>
        <p>Files are processed locally on this server.</p>
      </div>
    </nav>
  )

  return (
    <div className="flex h-full">
      <aside className="hidden w-56 shrink-0 border-r border-line bg-panel md:block">{sidebar}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/50" onClick={() => setMobileOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-60 border-r border-line bg-panel">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
          <button
            className="cursor-pointer rounded-md p-1.5 text-fg-2 hover:bg-panel-2 md:hidden"
            onClick={() => setMobileOpen((o) => !o)}
            aria-label="Toggle navigation"
          >
            {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <div className="ml-auto flex items-center gap-1">
            <button
              onClick={() => setTheme(isDark ? 'light' : 'dark')}
              className="cursor-pointer rounded-md p-1.5 text-fg-2 hover:bg-panel-2 hover:text-fg"
              aria-label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
              title="Toggle theme"
            >
              {isDark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1200px] px-4 py-6 md:px-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
