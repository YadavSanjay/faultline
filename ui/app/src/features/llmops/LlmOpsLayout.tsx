import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { cn } from "@/lib/cn";

// Operations console: LLM cost & RAG operational visibility. Aggregates the scenariogen
// LLM/dual-RAG cost + call traces that had no cross-run surface.

const TABS = [
  { to: "/operations", label: "LLM Cost", end: true },
  { to: "/operations/runs", label: "Runs & Traces" },
  { to: "/operations/health", label: "RAG Health" },
];

export function LlmOpsLayout() {
  return (
    <div>
      <PageHeader
        title="Operations"
        subtitle="LLM spend, per-stage & per-service cost, generation runs, and RAG service health."
        right={<LiveStoredToggle />}
      />
      <div className="mb-5 flex items-center gap-1 border-b border">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                isActive
                  ? "border-primary text-primary"
                  : "border-transparent text-fg-secondary hover:text-fg",
              )
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
