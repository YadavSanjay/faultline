import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { cn } from "@/lib/cn";

const TABS = [
  { to: "/marketdata", label: "Overview", end: true },
  { to: "/marketdata/catalog", label: "Feed Catalog" },
  { to: "/marketdata/coverage", label: "Coverage" },
  { to: "/marketdata/dq", label: "Data Quality" },
];

export function MarketDataLayout() {
  return (
    <div>
      <PageHeader title="Market Data" subtitle="Feeds, sources, freshness, and their linkage to the portfolio." />
      <div className="mb-5 flex items-center gap-1 border-b border">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                isActive ? "border-primary text-primary" : "border-transparent text-fg-secondary hover:text-fg",
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
