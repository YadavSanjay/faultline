import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { cn } from "@/lib/cn";

const TABS = [
  { to: "/sizing", label: "Overview", end: true },
  { to: "/sizing/rebalance", label: "Rebalance" },
  { to: "/sizing/frontier", label: "Frontier" },
  { to: "/sizing/methodology", label: "Methodology" },
];

export function SizingLayout() {
  return (
    <div>
      <PageHeader
        title="Portfolio Construction"
        subtitle="Re-sizing the book to a risk-appetite mandate — balanced, aggressive growth, or highly levered — so every position size has a defensible rationale."
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
