import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { cn } from "@/lib/cn";

// Risk workspace shell: a tab bar over the RK-B* pages, all reading the same stored risk
// report (React Query dedupes the fetch across tabs).

const TABS = [
  { to: "/risk", label: "Dashboard", end: true },
  { to: "/risk/drill", label: "Drill" },
  { to: "/risk/var-family", label: "VaR Family" },
  { to: "/risk/methodology", label: "Methodology" },
  { to: "/risk/pnl-explain", label: "P&L Explain" },
  { to: "/risk/backtest", label: "Backtest" },
  { to: "/risk/limits", label: "Limits" },
];

export function RiskLayout() {
  return (
    <div>
      <PageHeader
        title="Risk"
        subtitle="VaR/ES, sensitivities, scenarios, and controls — from the latest stored risk run."
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
