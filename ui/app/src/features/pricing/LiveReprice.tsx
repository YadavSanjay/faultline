import { useLiveReprice } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricGrid } from "@/components/shared/MetricGrid";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { humanizeUsd, exactUsd, fmtTimestamp } from "@/lib/format";
import { buildMeasures } from "./measures";
import { cn } from "@/lib/cn";

// Live re-price: the trade's fresh value from the pricer service's in-memory snapshot, next
// to the stored batch value. When the service isn't running it says so (and how to start it),
// rather than failing — the platform is stored-first and this is the opt-in live view.

export function LiveReprice({
  tradeId,
  tradeType,
  storedPv,
  storedSnapshot,
  valuationDate,
}: {
  tradeId: string;
  tradeType: string;
  storedPv: number;
  storedSnapshot?: string;
  valuationDate?: string;
}) {
  const query = useLiveReprice(tradeId, valuationDate, true);
  const data = query.data;

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Live re-price
            <span className="rounded bg-cat-teal-tint px-1.5 py-0.5 text-nano font-medium text-cat-teal-fg">live</span>
          </span>
        }
        subtitle="priced now against the pricer service's in-memory snapshot"
        right={
          <button
            type="button"
            onClick={() => query.refetch()}
            disabled={query.isFetching}
            className="rounded border border-strong px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-2 disabled:opacity-50"
          >
            {query.isFetching ? "Pricing…" : "Re-price"}
          </button>
        }
      />
      <CardBody>
        {query.isLoading ? (
          <div className="text-sm text-fg-secondary">Re-pricing live…</div>
        ) : !data ? (
          <WarningBanner tone="error" title="Could not reach the BFF">
            {(query.error as Error)?.message}
          </WarningBanner>
        ) : !data.available ? (
          data.service === "down" ? (
            <WarningBanner tone="info" title="Pricer service is not running">
              Live re-price needs the pricing service. Start it with{" "}
              <code>uvicorn src.pricer.service:app --port 8080</code> (or set <code>QLP_SERVICE_URL</code>), then
              Re-price. Everything else on this page is the stored batch result.
            </WarningBanner>
          ) : (
            <WarningBanner tone="error" title="Live re-price failed">{data.error}</WarningBanner>
          )
        ) : (
          (() => {
            const live = data.result!;
            const delta = live.pv - storedPv;
            const same = Math.abs(delta) < 1e-6;
            const measures = buildMeasures(tradeType, live.measures);
            const snapMatch = storedSnapshot && data.snapshot_id === storedSnapshot;
            return (
              <div className="space-y-4">
                <div className="flex flex-wrap items-end gap-8">
                  <div>
                    <div className="text-xs uppercase tracking-wide text-fg-muted">Live PV</div>
                    <div className={cn("tnum text-3xl font-semibold", live.pv >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(live.pv)}>
                      {humanizeUsd(live.pv)}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wide text-fg-muted">Δ vs stored</div>
                    {same ? (
                      <div className="text-lg font-medium text-fg-secondary">matches stored</div>
                    ) : (
                      <div className={cn("tnum text-2xl font-semibold", delta >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(delta)}>
                        {delta >= 0 ? "▲ " : "▼ "}{humanizeUsd(Math.abs(delta))}
                      </div>
                    )}
                    <div className="text-micro text-fg-muted">stored {humanizeUsd(storedPv)}</div>
                  </div>
                </div>

                <div className="rounded-md bg-surface-2 px-3 py-2 text-micro text-fg-secondary">
                  {snapMatch
                    ? "Live snapshot matches the stored run — identical inputs, so the values agree (a consistency check)."
                    : "Live snapshot differs from the stored run — the difference is the mark move since the batch."}
                </div>

                {live.warnings.length > 0 && (
                  <WarningBanner tone="warn" title={`${live.warnings.length} warning(s)`}>{live.warnings.join(" · ")}</WarningBanner>
                )}

                {measures.length > 0 && (
                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">Key measures (live)</div>
                    <MetricGrid metrics={measures} columns={4} />
                  </div>
                )}

                <div className="flex items-center justify-between border-t border-subtle pt-3 text-micro text-fg-muted">
                  <ProvenanceBadge items={{ snapshot_id: data.snapshot_id }} />
                  <span>priced {fmtTimestamp(data.priced_at)}</span>
                </div>
              </div>
            );
          })()
        )}
      </CardBody>
    </Card>
  );
}
