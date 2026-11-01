import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFeedDetail, useFeedSeries } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { SeriesChart } from "@/components/shared/SeriesChart";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { ConsumedByBadge, DqVerdictBadge, staleTone } from "./dq";
import { FeedDataTable } from "./FeedDataTable";
import { cn } from "@/lib/cn";

function bytes(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} KB`;
  return `${n} B`;
}

// MD-B3 Data Viewer (inline in the detail page).
function DataViewer({ feedId }: { feedId: string }) {
  const query = useFeedSeries(feedId);
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    if (query.data && selected.length === 0) {
      setSelected(query.data.numeric_columns.slice(0, 2));
    }
  }, [query.data, selected.length]);

  return (
    <Card>
      <CardHeader title="Data viewer" subtitle="down-sampled series — pick numeric columns to plot" />
      <CardBody>
        <QueryState query={query} empty={(d) => d.rows.length === 0}>
          {(data) => (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {data.numeric_columns.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setSelected((s) => (s.includes(c) ? s.filter((x) => x !== c) : [...s, c]))}
                    className={cn(
                      "rounded border px-2 py-0.5 text-xs",
                      selected.includes(c) ? "border-primary bg-primary text-primary-fg" : "border text-fg-secondary hover:bg-surface-2",
                    )}
                  >
                    {c}
                  </button>
                ))}
              </div>
              {selected.length > 0 ? (
                <SeriesChart rows={data.rows} dateCol={data.date_col} columns={selected} />
              ) : (
                <div className="rounded bg-surface-2 px-3 py-2 text-xs text-fg-secondary">Select a column to plot.</div>
              )}
              <div className="text-micro text-fg-muted">{data.rows.length} sampled rows · columns: {data.columns.join(", ")}</div>
            </div>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

export function FeedDetail() {
  const params = useParams();
  const feedId = params["*"] ?? "";
  const query = useFeedDetail(feedId);

  return (
    <div className="space-y-4">
      <nav className="text-xs text-fg-secondary">
        <Link to="/marketdata" className="hover:underline">
          Market Data
        </Link>
        <span className="mx-1 text-fg-muted">›</span>
        <span className="font-mono">{feedId}</span>
      </nav>

      <QueryState query={query}>
        {(feed) => (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h1 className="text-lg font-semibold text-fg">{feed.name}</h1>
                <div className="text-xs text-fg-muted">
                  <span className="capitalize">{feed.asset_class}</span> · {feed.file}
                </div>
              </div>
              <DqVerdictBadge verdict={feed.dq.verdict} />
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {/* source linkage */}
              <Card>
                <CardHeader title="Source" />
                <CardBody className="space-y-1 text-xs text-fg-secondary">
                  {Object.entries(feed.source)
                    .filter(([k]) => ["source_key", "name", "base_url", "auth", "timeout_s", "rate_limit_delay_s", "description", "enabled"].includes(k))
                    .map(([k, v]) => (
                      <div key={k}>
                        <span className="text-fg-muted">{k}:</span> {typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </div>
                    ))}
                </CardBody>
              </Card>

              {/* provenance */}
              <Card>
                <CardHeader title="Provenance" right={<ProvenanceBadge items={{ snapshot_id: feed.provenance.sha256 ?? undefined }} />} />
                <CardBody className="space-y-1 text-xs text-fg-secondary">
                  <div><span className="text-fg-muted">sha256:</span> <span className="font-mono">{feed.provenance.sha256 ? feed.provenance.sha256.slice(0, 24) + "…" : "— (not in snapshot)"}</span></div>
                  <div><span className="text-fg-muted">in snapshot:</span> {feed.provenance.in_snapshot ? "yes" : "no"}</div>
                  <div><span className="text-fg-muted">size:</span> {bytes(feed.provenance.size_bytes)} · <span className="text-fg-muted">rows:</span> {feed.provenance.row_count?.toLocaleString() ?? "—"}</div>
                  <div><span className="text-fg-muted">range:</span> {feed.provenance.date_min ?? "—"} → {feed.provenance.date_max ?? "—"} <span className={cn("ml-1", staleTone(feed.provenance.staleness_days))}>({feed.provenance.staleness_days != null ? `${feed.provenance.staleness_days}d old` : "no dates"})</span></div>
                  <div><span className="text-fg-muted">mtime:</span> {feed.provenance.mtime}</div>
                  <div className="pt-1"><span className="text-fg-muted">columns:</span> {feed.provenance.columns.join(", ")}</div>
                </CardBody>
              </Card>

              {/* data quality */}
              <Card>
                <CardHeader title="Data quality" subtitle={feed.dq.gated ? "declared in the post-ingest gate" : "not declared in the gate"} />
                <CardBody>
                  {feed.dq.checks.length === 0 ? (
                    <div className="text-xs text-fg-muted">
                      {feed.dq.note ?? "This feed isn't gated (not consumed by the pricer/risk path) — 'ungated' means unpoliced, not unusable."}
                    </div>
                  ) : (
                    <div className="space-y-1">
                      {feed.dq.checks.map((c) => (
                        <div key={c.check} className="flex items-center justify-between text-xs">
                          <span className="text-fg-secondary">{c.check}</span>
                          <span className="flex items-center gap-2">
                            <span className="text-fg-muted">{c.message}</span>
                            <span className={cn(c.severity === "ok" ? "text-success-fg" : c.severity === "warn" ? "text-warning-fg" : "text-danger-fg")}>
                              {c.severity}
                            </span>
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardBody>
              </Card>

              {/* consumption — what actually reads this feed, and the trades it prices */}
              <Card>
                <CardHeader
                  title="Consumption"
                  subtitle="what reads this feed & the trades it prices"
                  right={<ConsumedByBadge consumedBy={feed.consumption.consumed_by} />}
                />
                <CardBody className="space-y-2">
                  <div className="text-xs text-fg-secondary">{feed.consumption.role}</div>
                  {feed.consumption.pv_trades.length > 0 ? (
                    <div>
                      <div className="mb-1 text-micro uppercase tracking-wide text-fg-muted">
                        PV trades ({feed.consumption.n_pv_trades})
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {feed.consumption.pv_trades.map((t) => (
                          <Link
                            key={t.trade_id}
                            to={`/pricing/trade/${t.trade_id}`}
                            title={t.description}
                            className="rounded border border px-1.5 py-0.5 font-mono text-micro text-fg-secondary hover:bg-surface-2"
                          >
                            {t.trade_id}
                          </Link>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="text-xs text-fg-muted">
                      No trade prices off this feed{feed.consumption.consumed_by === "risk" ? " — it is a VaR/risk factor only." : "."}
                    </div>
                  )}
                </CardBody>
              </Card>
            </div>

            <DataViewer feedId={feedId} />
            <FeedDataTable feedId={feedId} />
          </div>
        )}
      </QueryState>
    </div>
  );
}
