import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useCoverage } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";

// MD-B4 Feed↔Trade Coverage: which trades depend on which feeds. V1 is asset-class-level
// linkage (a trade depends on its asset class's feeds) — the bridge into Pricing lineage.
export function Coverage() {
  const query = useCoverage();

  const tradesByAc = useMemo(() => {
    const m = new Map<string, { trade_id: string; description: string }[]>();
    for (const t of query.data?.trades ?? []) {
      const g = m.get(t.asset_class) ?? [];
      g.push(t);
      m.set(t.asset_class, g);
    }
    return m;
  }, [query.data]);

  return (
    <QueryState query={query}>
      {(data) => {
        const classes = Object.keys(data.feeds_by_asset_class).sort();
        return (
          <div className="space-y-4">
            <WarningBanner tone="info" title="Asset-class-level linkage">
              A trade depends on the feeds of its asset class. Per-feed precision is available for swaps and cash
              equity via the Pricing lineage (the market-data inputs node).
            </WarningBanner>
            {classes.map((ac) => {
              const feeds = data.feeds_by_asset_class[ac] ?? [];
              const trades = tradesByAc.get(ac) ?? [];
              return (
                <Card key={ac}>
                  <div className="border-b border-subtle px-4 py-2 text-sm font-semibold capitalize text-fg">{ac}</div>
                  <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">Trades ({trades.length})</div>
                      <div className="flex flex-wrap gap-1.5">
                        {trades.map((t) => (
                          <Link key={t.trade_id} to={`/pricing/trade/${t.trade_id}`} title={t.description} className="rounded border border px-1.5 py-0.5 font-mono text-micro text-fg-secondary hover:bg-surface-2">
                            {t.trade_id}
                          </Link>
                        ))}
                        {trades.length === 0 && <span className="text-xs text-fg-muted">no priced trades</span>}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">Feeds ({feeds.length})</div>
                      <div className="flex flex-wrap gap-1.5">
                        {feeds.map((fid) => (
                          <Link key={fid} to={`/marketdata/feed/${fid}`} className="rounded border border px-1.5 py-0.5 font-mono text-micro text-fg-secondary hover:bg-surface-2">
                            {fid.split("/").pop()}
                          </Link>
                        ))}
                      </div>
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        );
      }}
    </QueryState>
  );
}
