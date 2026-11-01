import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { cn } from "@/lib/cn";

// Governance & Controls — the trust control room, consolidated.
const TABS = [
  { to: "/governance", label: "Control Room", end: true },
  { to: "/governance/verify", label: "Verify" },
  { to: "/governance/drift", label: "Drift" },
  { to: "/governance/inventory", label: "Model Inventory" },
  { to: "/governance/signoff", label: "Sign-off" },
];

export function GovernanceLayout() {
  return (
    <div>
      <PageHeader title="Governance & Controls" subtitle="Is the book's pricing under control? — golden, IPV, inventory, limits, drift, sign-off." />
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
