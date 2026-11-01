import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { cn } from "@/lib/cn";

// P6 / B6: Market Signals — what the option market is pricing (implied vol term structure,
// skew, variance-risk premium, positioning, the risk-neutral density with tail probabilities,
// and an option-implied credit spread), surfaced from the P1 signal feeds.

const TABS = [
  { to: "/signals", label: "Signal Catalog", end: true },
  { to: "/signals/credit", label: "Implied Credit" },
  { to: "/signals/news", label: "News & Events" },
];

export function SignalsLayout() {
  return (
    <div>
      <PageHeader
        title="Market Signals"
        subtitle="Option-implied intelligence — what the market is pricing, alongside the book."
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
