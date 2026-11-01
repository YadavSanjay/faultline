# Faultline — UI (`ui/app`)

React explorer over the four data-pipeline subsystems (Market Data, Pricing, Risk, Stress
Scenarios) plus Portfolio Construction and the cross-cutting LLM Cost & Ops, System/Ops,
Lineage, and Governance areas. Two lenses — **Business** and **Technical** — toggled from the top
bar.

**Operating this UI (ports, run order, endpoints, what lights up each page):** see
[docs/guides/ui_guide.md](../../docs/guides/ui_guide.md). Design docs and the (now historical)
phased build plan live under [ui/docs/](../docs/).

## Stack

Vite · React 18 · TypeScript · React Router 6 · TanStack Query 5 · Tailwind CSS 3 · ECharts 5
(SVG renderer, theme-aware). The BFF API client is **hand-typed** in `src/lib/api.ts` (not
generated). No React Flow / shadcn.

**Design system:** semantic Tailwind tokens (`surface` / `fg` / `border` / `primary` / semantic
+ categorical) map to CSS variables in `src/index.css`; dark mode via `[data-theme="dark"]`.
Convention: components never use raw `slate-*` — always a role token. Charts are theme-aware via
`lib/chartTheme.ts` + `components/shared/EChart.tsx`.

## Run (dev)

The UI uses Node v24.14.0; set `$node` to its install folder (or call `npm` directly if it is on PATH).

```powershell
$node = "<node-v24.14.0-install-dir>"
& "$node\npm.cmd" install
& "$node\npm.cmd" run dev      # http://localhost:5173
```

Start the BFF first (separate terminal) so `/api` resolves — see [`../bff/README.md`](../bff/README.md).
The dev server proxies `/api` → `http://127.0.0.1:8088` (override with `VITE_BFF_TARGET`).
Scripts: `dev`, `build` (`tsc -b && vite build`), `preview`, `typecheck`, `test` (vitest).

> The UI is a view over gitignored pipeline outputs — pages are empty until the runners populate
> `data/`. See the runner prerequisite chain in [ui_guide.md](../../docs/guides/ui_guide.md).

## Layout

```
src/
  app/routes.tsx           route table (lens-aware landing: business→/cockpit, technical→/ops)
  components/shell/         TopBar, SideNav, LensToggle, ThemeToggle, AppShell
  components/shared/        ProvenanceBadge, MetricGrid, Zone, DqBadge, WarningBanner,
                            LiveStoredToggle, Markdown, Heatmap, EChart + chart components,
                            table/DataTable
  components/ui/            Card, LinkButton primitives
  features/                 cockpit, pricing, risk, marketdata, scenarios, sizing, llmops,
                            governance, ops, system, lineage, admin
  lib/                      api (hand-typed client + React Query hooks), theme, lens, spine,
                            format, cn, chartTheme
```
