import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { cn } from "@/lib/cn";
import { UnderTest } from "./UnderTest";

const TABS = [
  { to: "/scenarios", label: "Catalog", end: true },
  { to: "/scenarios/reverse", label: "Reverse Stress" },
  { to: "/scenarios/ablation", label: "Layer Ablation" },
  { to: "/scenarios/signal-health", label: "Signal Health" },
  { to: "/scenarios/measurement", label: "Measurement" },
  { to: "/scenarios/versions", label: "Versions" },
  { to: "/scenarios/signoff", label: "Sign-off" },
];

export function ScenarioLayout() {
  return (
    <div>
      <PageHeader
        title="Stress Scenarios"
        subtitle="Named scenarios × severity tiers — what each tests, how its shocks were derived, and what it does to the book."
        right={<LiveStoredToggle />}
      />
      <UnderTest />
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
