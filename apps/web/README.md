# ArtifactDiff web UI

React + TypeScript + Vite + Tailwind CSS (shadcn-style components in `src/components/ui`), TanStack Query,
Zustand, Monaco (bundled locally), Three.js and Recharts. It talks only to the REST API.

```bash
npm ci
npm run dev        # http://localhost:5173, proxies /api to http://127.0.0.1:8000 (run `artifactdiff serve`)
npm run build      # then: python ../../scripts/build_web.py --skip-npm
npm run e2e        # Playwright: boots the real API serving dist/ (build first)
```
