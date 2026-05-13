# octowright-frontend

TypeScript web debugger UI for octowright. Built with Vite into
the Python server's static-file directory (`src/octowright/server/frontend/`).

## Layout

- `src/` — TypeScript modules. Entry points `dashboard.ts` and `session.ts`.
- `static/` — `index.html`, `session.html`, `styles.css`. Copied verbatim by the
  build step.
- Tests are co-located (`*.test.ts`) and run with vitest under jsdom.

## Scripts

```
npm install
npm run typecheck    # tsc --noEmit
npm run lint         # biome lint
npm run test         # vitest run
npm run build        # vite build into src/octowright/server/frontend/
npm run fix          # biome check --write --unsafe
```

The `build` script uses Vite multi-entry HTML input from `static/` and emits
flattened bundle assets into `../../src/octowright/server/frontend/`, which is
the directory the Python Starlette server mounts.

## API contract

The frontend talks to the Starlette backend (built in parallel by a sibling
agent) via the JSON / WebSocket endpoints documented in `src/api.ts`. Types are
in `src/types.ts` and must remain in sync with the Python pydantic models.
