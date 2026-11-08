import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ComparePage } from './pages/Compare'
import { Dashboard } from './pages/Dashboard'
import { DocsPage } from './pages/Docs'
import { HistoryPage } from './pages/History'
import { NotFound } from './pages/NotFound'
import { PluginsPage } from './pages/Plugins'
import { ResultPage } from './pages/Result'
import { SettingsPage } from './pages/Settings'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="comparisons/:id" element={<ResultPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="plugins" element={<PluginsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="docs" element={<DocsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
