import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  useMdOverview,
  useMdDependency,
  type MdOverviewFeed,
  type DqVerdict,
} from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { DqVerdictBadge, ConsumedByBadge } from "./dq";
import { cn } from "@/lib/cn";

// MD overview: the market-data landing — freshness + DQ heatmap, consumption summary, and
// the feed→trade dependency ("blast radius"). Every cell drills into a feed; the catalog
// tab holds the full sortable list.

const DQ_CELL: Record<DqVerdict, string> = {
  ok: "bg-success/80 hover:bg-success",
  warn: "bg-warning/80 hover:bg-warning",
  block: "bg-danger/80 hover:bg-danger",
  // ungated = no DQ gate: an outlined cell (visible in both themes) rather than a
  // near-invisible fill.
  ungated: "bg-surface-2 ring-1 ring-inset ring-border-strong hover:bg-border-strong",
};

function feedHref(id: string): string {
  return `/marketdata/feed/${id}`;
}

function HeatCell({ f }: { f: MdOverviewFeed }) {
  const title = `${f.name} · DQ ${f.dq}${f.staleness_days != null ? ` · ${f.staleness_days}d old` : ""}${
    f.stale ? " (stale)" : ""
  } · ${f.role}`;
  return (
    <Link
      to={feedHref(f.id)}
      title={title}
      className={cn(
        "relative flex h-7 w-7 items-center justify-center rounded text-nano font-medium text-white/0",
        DQ_CELL[f.dq],
        f.consumed_by === "none" && "opacity-60",
      )}
    >
      {f.stale && <span className="absolute inset-0 rounded ring-2 ring-inset ring-warning" />}
    </Link>
  );
}

export function MarketDataOverview() {
  const overview = useMdOverview();
  const dep = useMdDependency();
  const [openFeed, setOpenFeed] = useState<string | null>(null);

  const depSorted = useMemo(
    () => [...(dep.data?.feeds ?? [])].sort((a, b) => b.n_trades - a.n_trades),
    [dep.data],
  );

  return (
    <QueryState query={overview}>
      {(o) => {
        const byAc = new Map<string, MdOverviewFeed[]>();
        for (const f of o.feeds) {
          const arr = byAc.get(f.asset_class) ?? [];
          arr.push(f);
          byAc.set(f.asset_class, arr);
        }
        const metrics: Metric[] = [
          { label: "Feeds", value: o.n_feeds, caption: `${o.n_asset_classes} asset classes` },
          {
            label: "Consumed",
            value: `${o.n_consumed}`,
            caption: `${o.n_unused} downloaded but unused`,
          },
          {
            label: "Stale",
            value: o.n_stale,
            tone: o.n_stale ? "loss" : "neutral",
            caption: o.stale_cutoff_days != null ? `> ${o.stale_cutoff_days}d old` : "no cutoff",
          },
          {
            label: "Data quality",
            value: `${o.dq_counts.ok} ok`,
            tone: o.dq_counts.block ? "loss" : o.dq_counts.warn ? "neutral" : "gain",
            caption: `${o.dq_counts.warn} warn · ${o.dq_counts.block} blocked · ${o.dq_counts.ungated} ungated`,
          },
        ];

        return (
          <div className="space-y-5">
            <Card>
              <CardBody>
                <MetricGrid metrics={metrics} columns={4} />
              </CardBody>
            </Card>

            {/* freshness + DQ heatmap */}
            <Card>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-2">
                <h3 className="text-sm font-semibold text-fg">Freshness &amp; data-quality heatmap</h3>
                <div className="flex items-center gap-3 text-micro text-fg-secondary">
                  <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-success/80" /> ok</span>
                  <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-warning/80" /> warn</span>
                  <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-danger/80" /> block</span>
                  <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-surface-2" /> ungated</span>
                  <span className="flex items-center gap-1"><span className="h-3 w-3 rounded ring-2 ring-inset ring-warning" /> stale</span>
                </div>
              </div>
              <CardBody className="space-y-2">
                {[...byAc.entries()]
                  .sort((a, b) => a[0].localeCompare(b[0]))
                  .map(([ac, feeds]) => (
                    <div key={ac} className="flex items-center gap-3">
                      <div className="w-20 shrink-0 truncate text-xs capitalize text-fg-secondary">{ac}</div>
                      <div className="flex flex-wrap gap-1">
                        {feeds.map((f) => (
                          <HeatCell key={f.id} f={f} />
                        ))}
                      </div>
                    </div>
                  ))}
                <div className="pt-1 text-micro text-fg-muted">
                  Dimmed cells are downloaded feeds nothing prices or risks (reference / redundant / roadmap).
                  Click a cell for the feed detail.
                </div>
              </CardBody>
            </Card>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* consumption breakdown */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Consumption
                </div>
                <CardBody className="space-y-2">
                  {([
                    ["both", "PV + Risk", "bg-cat-indigo-fg"],
                    ["pv", "PV only", "bg-success"],
                    ["risk", "Risk only", "bg-info"],
                    ["none", "Not consumed", "bg-border-strong"],
                  ] as const).map(([key, label, color]) => {
                    const n = o.consumption_counts[key] ?? 0;
                    return (
                      <div key={key} className="flex items-center gap-3">
                        <div className="w-24 shrink-0 text-xs text-fg-secondary">{label}</div>
                        <div className="h-3 flex-1 overflow-hidden rounded bg-surface-0">
                          <div className={cn("h-full rounded", color)} style={{ width: `${(n / o.n_feeds) * 100}%` }} />
                        </div>
                        <div className="tnum w-8 shrink-0 text-right text-xs text-fg-secondary">{n}</div>
                      </div>
                    );
                  })}
                </CardBody>
              </Card>

              {/* per-asset-class table */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  By asset class
                </div>
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                      <tr>
                        <th className="px-4 py-1.5 font-medium">Asset class</th>
                        <th className="px-4 py-1.5 text-right font-medium">Feeds</th>
                        <th className="px-4 py-1.5 text-right font-medium">Consumed</th>
                        <th className="px-4 py-1.5 text-right font-medium">Stale</th>
                        <th className="px-4 py-1.5 font-medium">Worst DQ</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {o.by_asset_class.map((r) => (
                        <tr key={r.asset_class} className="hover:bg-surface-2">
                          <td className="px-4 py-1.5 capitalize text-fg">{r.asset_class}</td>
                          <td className="px-4 py-1.5 text-right tnum text-fg-secondary">{r.n_feeds}</td>
                          <td className="px-4 py-1.5 text-right tnum text-fg-secondary">{r.n_consumed}</td>
                          <td className={cn("px-4 py-1.5 text-right tnum", r.n_stale ? "text-warning-fg" : "text-fg-muted")}>
                            {r.n_stale}
                          </td>
                          <td className="px-4 py-1.5"><DqVerdictBadge verdict={r.worst_dq} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>

            {/* feed→trade dependency (blast radius) */}
            <Card>
              <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
                <h3 className="text-sm font-semibold text-fg">
                  Feed dependencies <span className="font-normal text-fg-muted">· which trades break if a feed goes stale</span>
                </h3>
                {dep.data && (
                  <span className="text-xs text-fg-muted">
                    {dep.data.n_feeds_consumed} consumed feeds · {dep.data.n_trades} trades
                  </span>
                )}
              </div>
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-4 py-1.5 font-medium">Feed</th>
                      <th className="px-4 py-1.5 font-medium">Consumed by</th>
                      <th className="px-4 py-1.5 font-medium">DQ</th>
                      <th className="px-4 py-1.5 text-right font-medium">Trades</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {depSorted.map((f) => (
                      <Fragment key={f.id}>
                        <tr
                          className="cursor-pointer hover:bg-surface-2"
                          onClick={() => setOpenFeed(openFeed === f.id ? null : f.id)}
                        >
                          <td className="px-4 py-1.5">
                            <span className="text-fg-muted">{f.n_trades ? (openFeed === f.id ? "▾ " : "▸ ") : "  "}</span>
                            <span className="font-mono text-xs text-fg">{f.id}</span>
                          </td>
                          <td className="px-4 py-1.5"><ConsumedByBadge consumedBy={f.consumed_by} /></td>
                          <td className="px-4 py-1.5"><DqVerdictBadge verdict={f.dq} /></td>
                          <td className="px-4 py-1.5 text-right tnum text-fg-secondary">
                            {f.is_risk_factor ? <span className="text-micro text-fg-muted">book factor</span> : f.n_trades}
                          </td>
                        </tr>
                        {openFeed === f.id && f.trades.length > 0 && (
                          <tr className="bg-surface-2/60">
                            <td colSpan={4} className="px-4 py-2">
                              <div className="mb-1 text-micro text-fg-secondary">{f.role}</div>
                              <div className="flex flex-wrap gap-1">
                                {f.trades.map((t) => (
                                  <Link
                                    key={t.trade_id}
                                    to={`/pricing/trade/${t.trade_id}`}
                                    title={t.description}
                                    className="rounded border border bg-surface-1 px-1.5 py-0.5 font-mono text-micro text-fg-secondary hover:bg-surface-0"
                                  >
                                    {t.trade_id}
                                  </Link>
                                ))}
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <div className="text-xs text-fg-secondary">
              Drill down: <Link to="/marketdata/catalog" className="text-fg-secondary underline">feed catalog</Link> → a
              feed → its series &amp; provenance.
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}
