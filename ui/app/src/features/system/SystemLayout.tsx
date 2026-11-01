import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { cn } from "@/lib/cn";

// System / Ops console — the technical-lens home. Sub-nav over the board, run history,
// run trace, pipeline DAG, module map, and config.

const TABS = [
  { to: "/ops", label: "Board", end: true },
  { to: "/ops/runs", label: "Run History" },
  { to: "/ops/jobs", label: "Jobs" },
  { to: "/ops/pipeline", label: "Pipeline" },
  { to: "/ops/modules", label: "Modules" },
  { to: "/ops/config", label: "Config" },
];

export function SystemLayout() {
  return (
    <div>
      <PageHeader title="System / Ops" subtitle="Health, runs, end-to-end traces, the pipeline DAG, and the module map." />
      <div className="mb-5 flex items-center gap-1 border-b border">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium", isActive ? "border-primary text-primary" : "border-transparent text-fg-secondary hover:text-fg")
            }
          >
            {t.label}
          </NavLink>
        ))}
      </div>
      <Outlet />
    </div>
  );
}
