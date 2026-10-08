# Answer Script Evaluator — web app

Next.js (App Router) + TypeScript + Tailwind. Talks only to the FastAPI backend: `/api/*` is
proxied to `API_URL` (default `http://127.0.0.1:8000`), so the session cookie stays same-origin.

```bash
npm install
npm run dev          # http://localhost:3000 (start the backend first)
npm run gen:api      # regenerate src/lib/api-types.ts from the backend's OpenAPI schema
npm run test         # Vitest component tests
npm run e2e          # Playwright, against the demo backend (fake AI)
npm run lint && npm run typecheck
```

Backend for local work without AI keys: `python -m backend.app.demo --yes-demo` (repo root).
