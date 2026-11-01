import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useFeeds, type Feed } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { ConsumedByBadge, DqVerdictBadge, staleTone } from "./dq";
import { cn } from "@/lib/cn";

// MD-B1 Feed Catalog: the single view of which feeds exist, whether the pricer/risk
// engines actually read them, and — for the ones they don't — why not.

const isPv = (f: Feed) => f.consumed_by === "pv" || f.consumed_by === "both";
const isRisk = (f: Feed) => f.consumed_by === "risk" || f.consumed_by === "both";

type UseFilter = "all" | "pv" | "risk" | "none";

function SummaryTiles({ feeds }: { feeds: Feed[] }) {
  const pv = feeds.filter(isPv).length;
  const risk = feeds.filter(isRisk).length;
  const none = feeds.filter((f) => f.consumed_by === "none").length;
  const tiles = [
    { n: feeds.length, label: "Feeds downloaded", sub: "across all asset classes", num: "text-fg", bar: "bg-fg-muted" },
    { n: pv, label: "Priced with (PV)", sub: "read at valuation time", num: "text-success-fg", bar: "bg-success" },
    { n: risk, label: "VaR / risk factor", sub: `${feeds.filter((f) => f.consumed_by === "both").length} of these also price`, num: "text-info-fg", bar: "bg-info" },
    { n: none, label: "Not consumed", sub: "reference · redundant · roadmap", num: "text-fg-secondary", bar: "bg-border-strong" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((t) => (
        <Card key={t.label} className="relative overflow-hidden">
          <div className={cn("absolute inset-y-0 left-0 w-1", t.bar)} />
          <div className="px-4 py-3">
            <div className={cn("tnum text-2xl font-semibold tabular-nums", t.num)}>{t.n}</div>
            <div className="mt-1 text-xs text-fg-secondary">{t.label}</div>
            <div className="text-micro text-fg-muted">{t.sub}</div>
          </div>
        </Card>
      ))}
    </div>
  );
}

function WhyPanel() {
  const cols = [
    {
      tag: "Data-quality defect · 1 feed",
      tone: "text-danger-fg bg-danger-tint",
      title: "The data let us down",
      body: "The free DTCC corporate single-name CDS feed is ~76% NaN with misfiltered LBO names, so bonds #15–19 fell back to configured spreads. The only genuine “wanted it, data failed” case — now fixed: ORCL/META derive from the credit-desk single-name feed.",
    },
    {
      tag: "Methodology / scope",
      tone: "text-warning-fg bg-warning-tint",
      title: "The model doesn't consume it",
      body: "Risk-only feeds (FRED rates, BofA OAS, MOVE, VIX/SKEW) drive VaR, not PV. Others await a build — DTCC SDR IR-options is the intended SABR surface (PV uses a flat vol); the TWD NDF fixings are unused because the CCS holds the foreign rate constant.",
    },
    {
      tag: "Reference / redundant",
      tone: "text-fg-secondary bg-surface-0",
      title: "Never a pricing input",
      body: "FIGI identifiers, bond reference/prospectus data, corporate actions, realized-SOFR cross-checks, split-adjusted prices (raw close used instead), and the AI-power-demand stress narrative. Absence from pricing is correct.",
    },
  ];
  return (
    <details className="group rounded-lg border border bg-surface-1">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-sm font-medium text-fg">
        <span>Why isn't every downloaded feed used in pricing?</span>
        <span className="text-xs text-fg-muted group-open:hidden">show ›</span>
        <span className="hidden text-xs text-fg-muted group-open:inline">hide ⌄</span>
      </summary>
      <div className="border-t border-subtle px-4 py-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {cols.map((c) => (
            <div key={c.title} className="rounded-md border border-subtle p-3">
              <span className={cn("inline-block rounded px-1.5 py-0.5 text-nano font-semibold uppercase tracking-wide", c.tone)}>{c.tag}</span>
              <div className="mt-2 text-sm font-medium text-fg">{c.title}</div>
              <p className="mt-1 text-xs leading-relaxed text-fg-secondary">{c.body}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-micro leading-relaxed text-fg-muted">
          Bonds credit-spread reconciliation (2026-07-11): ORCL&nbsp;#17 → 222bp and META&nbsp;#18 → 118.5bp now
          sourced from <span className="font-mono">credit/dtcc_single_name_cds.csv</span> (CDS par + ~45bp SOFR–Treasury
          swap spread); AAPL/MSFT/AMZN keep the config fallback. See{" "}
          <span className="font-mono">docs/design/AITrade_quantlib_portfolio_marketdata.md §5</span>.
        </p>
      </div>
    </details>
  );
}

function FeedTable({ feeds }: { feeds: Feed[] }) {
  return (
    <table className="w-full text-sm">
      <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
        <tr>
          <th className="px-4 py-2 font-medium">Feed</th>
          <th className="px-4 py-2 font-medium">Source</th>
          <th className="px-4 py-2 text-right font-medium">Rows</th>
          <th className="px-4 py-2 font-medium">Range</th>
          <th className="px-4 py-2 text-right font-medium">Stale</th>
          <th className="px-4 py-2 font-medium">DQ</th>
          <th className="px-4 py-2 font-medium">Used by</th>
          <th className="px-4 py-2 text-right font-medium">PV trades</th>
          <th className="px-4 py-2 font-medium">Role in pricing / why not</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border-subtle">
        {feeds.map((f) => (
          <tr key={f.id} className={cn("hover:bg-surface-2", f.consumed_by === "none" && "opacity-80")}>
            <td className="px-4 py-2">
              <Link to={`/marketdata/feed/${f.id}`} className="text-fg hover:underline">
                {f.name}
              </Link>
              <div className="font-mono text-micro text-fg-muted">{f.file}</div>
            </td>
            <td className="px-4 py-2 text-fg-secondary">{f.source}</td>
            <td className="px-4 py-2 text-right tnum text-fg-secondary">{f.row_count?.toLocaleString() ?? "—"}</td>
            <td className="px-4 py-2 tnum text-xs text-fg-secondary">
              {f.date_min && f.date_max ? `${f.date_min} → ${f.date_max}` : "—"}
            </td>
            <td className={cn("px-4 py-2 text-right tnum", staleTone(f.staleness_days))}>
              {f.staleness_days != null ? `${f.staleness_days}d` : "—"}
            </td>
            <td className="px-4 py-2">
              <DqVerdictBadge verdict={f.dq} />
            </td>
            <td className="px-4 py-2">
              <ConsumedByBadge consumedBy={f.consumed_by} />
            </td>
            <td className="px-4 py-2 text-right tnum text-fg-secondary">
              {f.consumed_by === "none" ? "—" : f.n_pv_trades}
            </td>
            <td className="px-4 py-2 text-xs text-fg-secondary" style={{ maxWidth: "34ch" }}>
              {f.role}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const USE_CHIPS: { key: UseFilter; label: string }[] = [
  { key: "all", label: "all uses" },
  { key: "pv", label: "PV" },
  { key: "risk", label: "Risk" },
  { key: "none", label: "not consumed" },
];

export function FeedCatalog() {
  const query = useFeeds();
  const [ac, setAc] = useState<string | null>(null);
  const [use, setUse] = useState<UseFilter>("all");

  const filtered = useMemo(() => {
    const feeds = query.data?.feeds ?? [];
    return feeds.filter((f) => {
      if (ac && f.asset_class !== ac) return false;
      if (use === "pv") return isPv(f);
      if (use === "risk") return isRisk(f);
      if (use === "none") return f.consumed_by === "none";
      return true;
    });
  }, [query.data, ac, use]);

  const grouped = useMemo(() => {
    const m = new Map<string, Feed[]>();
    for (const f of filtered) {
      const g = m.get(f.asset_class) ?? [];
      g.push(f);
      m.set(f.asset_class, g);
    }
    return m;
  }, [filtered]);

  return (
    <QueryState query={query} empty={(d) => d.feeds.length === 0}>
      {(data) => (
        <div className="space-y-4">
          <SummaryTiles feeds={data.feeds} />

          <div className="rounded-md border border bg-surface-2 px-4 py-2.5 text-xs leading-relaxed text-fg-secondary">
            <span className="font-medium text-fg">Reading this catalog.</span>{" "}
            <span className="font-medium">Used by</span> shows what actually reads each feed:{" "}
            <ConsumedByBadge consumedBy="pv" /> at valuation time,{" "}
            <ConsumedByBadge consumedBy="risk" /> as a VaR factor,{" "}
            <ConsumedByBadge consumedBy="both" />, or{" "}
            <ConsumedByBadge consumedBy="none" /> (downloaded for reference, or superseded / awaiting a
            methodology build). <span className="font-medium">PV trades</span> is the real feed→trade
            linkage (the specific trades priced off the feed), not a folder-wide count. A{" "}
            <span className="font-medium">DQ</span> of{" "}
            <span className="font-medium text-fg-secondary">ungated</span> means no data-quality rule is
            declared because nothing consumes it — it does <span className="font-medium">not</span> mean
            the data is bad or unused-in-error.
          </div>

          <WhyPanel />

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1">
              {USE_CHIPS.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => setUse(c.key)}
                  className={cn(
                    "rounded px-2.5 py-1 text-xs font-medium",
                    use === c.key ? "bg-primary text-primary-fg" : "bg-surface-1 text-fg-secondary hover:bg-surface-0",
                  )}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <span className="mx-1 h-4 w-px bg-surface-2" />
            <div className="flex flex-wrap gap-1">
              <button
                type="button"
                onClick={() => setAc(null)}
                className={cn("rounded px-2.5 py-1 text-xs font-medium", ac === null ? "bg-primary text-primary-fg" : "bg-surface-1 text-fg-secondary hover:bg-surface-0")}
              >
                all classes ({data.count})
              </button>
              {data.asset_classes.map((a) => (
                <button
                  key={a}
                  type="button"
                  onClick={() => setAc(a)}
                  className={cn("rounded px-2.5 py-1 text-xs font-medium capitalize", ac === a ? "bg-primary text-primary-fg" : "bg-surface-1 text-fg-secondary hover:bg-surface-0")}
                >
                  {a}
                </button>
              ))}
            </div>
          </div>

          {[...grouped.entries()].map(([klass, feeds]) => (
            <Card key={klass}>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold capitalize text-fg">
                {klass} <span className="font-normal text-fg-muted">· {feeds.length} feeds</span>
              </div>
              <div className="overflow-auto">
                <FeedTable feeds={feeds} />
              </div>
            </Card>
          ))}
          {grouped.size === 0 && (
            <Card>
              <div className="px-4 py-8 text-center text-sm text-fg-muted">No feeds match this filter.</div>
            </Card>
          )}
        </div>
      )}
    </QueryState>
  );
}
