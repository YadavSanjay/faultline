import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useLens } from "@/lib/lens";
import { cn } from "@/lib/cn";

// Left navigation, stress-first. The product question is "where does this book break?", so
// the first group reads top-down as that story: the overview, the portfolio under test, the scenarios run
// against it, its break points, and what drives the loss. Trust (signal evidence, versions)
// comes next, then the supporting subsystems. The technical tools (LLM cost/ops, system
// console, lineage, admin) live in a group collapsed in the Business lens and expanded in
// the Technical lens. Every section header collapses its group (remembered per browser).
// Routes are unchanged; several items deep-link into scenario tabs.

interface NavItem {
  to: string;
  label: string;
  // Active-state override. Default: the route or any route below it.
  match?: (path: string) => boolean;
}

interface NavSection {
  heading: string;
  items: NavItem[];
}

// Scenario sub-routes that have their own nav item; "Scenarios" owns every other
// /scenarios path (the catalog and the per-scenario detail pages).
const SCENARIO_TABS = ["/scenarios/reverse", "/scenarios/ablation", "/scenarios/signal-health", "/scenarios/versions", "/scenarios/signoff"];

const under = (prefix: string) => (path: string) => path === prefix || path.startsWith(`${prefix}/`);

const PRIMARY: NavSection[] = [
  {
    heading: "Where it breaks",
    items: [
      { to: "/overview", label: "Overview" },
      { to: "/pricing", label: "The Portfolio" },
      {
        to: "/scenarios",
        label: "Scenarios",
        match: (p) => under("/scenarios")(p) && !SCENARIO_TABS.some((t) => under(t)(p)),
      },
      { to: "/scenarios/reverse", label: "Break Points" },
      { to: "/scenarios/ablation", label: "What Drives It" },
    ],
  },
  {
    heading: "Can we trust it",
    items: [
      { to: "/scenarios/signal-health", label: "Signal Health" },
      { to: "/signals", label: "Market Signals" },
      {
        to: "/scenarios/versions",
        label: "Versions & Sign-off",
        match: (p) => under("/scenarios/versions")(p) || under("/scenarios/signoff")(p),
      },
    ],
  },
  {
    heading: "Behind the numbers",
    items: [
      { to: "/risk", label: "Baseline Risk" },
      { to: "/sizing", label: "Construction" },
      { to: "/marketdata", label: "Market Data" },
      { to: "/governance", label: "Governance" },
      { to: "/cockpit", label: "Cockpit" },
    ],
  },
];

const TECHNICAL: NavSection = {
  heading: "Technical",
  items: [
    { to: "/operations", label: "LLM Cost & Ops" },
    { to: "/ops", label: "System / Ops" },
    { to: "/lineage", label: "Lineage" },
    { to: "/admin", label: "Admin" },
  ],
};

// Collapsed sections, remembered per browser (a convenience — the nav works without it).
const COLLAPSED_KEY = "qlp.nav.collapsed";

function readCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? "{}") as Record<string, boolean>;
  } catch {
    return {};
  }
}

function isItemActive(item: NavItem, pathname: string): boolean {
  return (item.match ?? under(item.to))(pathname);
}

function Item({ item }: { item: NavItem }) {
  const { pathname } = useLocation();
  return (
    <NavLink
      to={item.to}
      className={cn(
        "flex items-center rounded-md px-3 py-1 text-body-sm",
        isItemActive(item, pathname) ? "bg-primary text-primary-fg" : "text-fg-secondary hover:bg-surface-0",
      )}
    >
      {item.label}
    </NavLink>
  );
}

function Section({
  section,
  open,
  onToggle,
}: {
  section: NavSection;
  open: boolean;
  onToggle: () => void;
}) {
  const { pathname } = useLocation();
  // a collapsed section still shows where you are
  const holdsActive = !open && section.items.some((item) => isItemActive(item, pathname));
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center justify-between rounded px-3 py-1 text-micro font-bold uppercase tracking-wider text-fg-secondary hover:text-fg"
      >
        <span className="flex items-center gap-1.5">
          {section.heading}
          {holdsActive && <span className="h-1.5 w-1.5 rounded-full bg-primary" aria-label="current page" />}
        </span>
        <span className="text-nano text-fg-muted">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="mt-0.5 space-y-0.5">
          {section.items.map((item) => (
            <Item key={item.to} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

export function SideNav() {
  const { lens } = useLens();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(readCollapsed);
  const [techOpen, setTechOpen] = useState(lens === "technical");
  // follow the lens: expand technical tools in the Technical lens, collapse in Business.
  useEffect(() => setTechOpen(lens === "technical"), [lens]);

  const toggle = (heading: string) =>
    setCollapsed((prev) => {
      const next = { ...prev, [heading]: !prev[heading] };
      try {
        localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next));
      } catch {
        // storage unavailable: keep the in-memory state
      }
      return next;
    });

  return (
    <nav className="w-56 shrink-0 space-y-4 border-r border bg-surface-1 px-3 py-4">
      {PRIMARY.map((section) => (
        <Section
          key={section.heading}
          section={section}
          open={!collapsed[section.heading]}
          onToggle={() => toggle(section.heading)}
        />
      ))}
      {/* technical tools — collapsed by default in the Business lens */}
      <Section section={TECHNICAL} open={techOpen} onToggle={() => setTechOpen((o) => !o)} />
    </nav>
  );
}
