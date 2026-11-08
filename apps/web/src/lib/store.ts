import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Theme = 'dark' | 'light' | 'system'

interface SettingsState {
  theme: Theme
  apiToken: string
  setTheme: (t: Theme) => void
  setApiToken: (t: string) => void
}

// Per-browser preferences only. Comparison data lives on the server, never in localStorage.
export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'dark',
      apiToken: '',
      setTheme: (theme) => set({ theme }),
      setApiToken: (apiToken) => set({ apiToken }),
    }),
    { name: 'artifactdiff-settings' },
  ),
)

export function applyTheme(theme: Theme): void {
  const dark =
    theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.classList.toggle('light', !dark)
}
