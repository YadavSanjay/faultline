import { NavLink, Outlet } from "react-router-dom";
import { PageHeader } from "@/components/shared/PageHeader";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { cn } from "@/lib/cn";

// Pricing workspace shell: an overview landing plus the book table and model inventory,
// all reading the same stored results store. Trade/lineage/history drilldowns open as
// full-screen routes outside this shell.

const TABS = [
  { to: "/pricing", label: "Overview", end: true },
  { to: "/pricing/book", label: "Book Valuation" },
  { to: "/pricing/inventory", label: "Model Inventory" },
];

export function PricingLayout() {
  return (
    <div>
      <PageHeader
        title="Pricing"
        subtitle="Portfolio value, composition, and the book's PV trend — from the latest stored runs."
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
