import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useBook, type BookTrade } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// PR-B1 Book Valuation: the 5-second "what's the book worth and is it healthy?" view.
// The trade grid uses the shared DataTable (sort / filter / paginate / CSV export).

function deltaCell(d: number | null) {
  if (d == null) return <span className="text-fg-muted">—</span>;
  const tone = d >= 0 ? "text-success-fg" : "text-danger-fg";
  const glyph = d >= 0 ? "▲" : "▼";
  return (
    <span className={cn("tnum", tone)} title={exactUsd(d)}>
      {glyph} {humanizeUsd(Math.abs(d))}
    </span>
  );
}

const COLUMNS: DataColumn<BookTrade>[] = [
  {
    key: "trade_id",
    header: "Trade",
    get: (t) => t.trade_id,
    filterable: true,
    render: (t) => (
      <div>
        <Link to={`/pricing/trade/${t.trade_id}`} className="font-mono text-fg hover:underline">
          {t.trade_id}
        </Link>
        <div className="max-w-xs truncate text-xs text-fg-muted" title={t.description}>
          {t.description}
        </div>
      </div>
    ),
    csv: (t) => t.trade_id,
  },
  {
    key: "asset_class",
    header: "Asset class",
    get: (t) => t.asset_class,
    filterable: true,
    render: (t) => <span className="capitalize text-fg-secondary">{t.asset_class}</span>,
  },
  { key: "trade_type", header: "Type", get: (t) => t.trade_type, render: (t) => <span className="text-fg-secondary">{t.trade_type}</span> },
  {
    key: "pv",
    header: "PV",
    align: "right",
    get: (t) => t.pv,
    csv: (t) => t.pv ?? "",
    render: (t) => (
      <span className={cn((t.pv ?? 0) >= 0 ? "text-fg" : "text-danger-fg")} title={exactUsd(t.pv ?? 0)}>
        {humanizeUsd(t.pv)}
      </span>
    ),
  },
  { key: "delta", header: "Δ", align: "right", get: (t) => t.delta, csv: (t) => t.delta ?? "", render: (t) => deltaCell(t.delta) },
  {
    key: "status",
    header: "Status",
    get: (t) => t.status_kind,
    csv: (t) => t.status,
    render: (t) => (
      <Zone
        status={t.status_kind === "ok" ? (t.n_warnings ? "amber" : "green") : t.status_kind === "blocked" ? "amber" : "red"}
        label={t.n_warnings && t.status_kind === "ok" ? `${t.n_warnings} warn` : t.status_kind}
      />
    ),
  },
];

export function BookValuation() {
  const [date, setDate] = useState<string | undefined>(undefined);
  const [acFilter, setAcFilter] = useState<string | null>(null);
  const query = useBook(date);

  const filteredTrades = useMemo(() => {
    const trades = query.data?.trades ?? [];
    return acFilter ? trades.filter((t) => t.asset_class === acFilter) : trades;
  }, [query.data, acFilter]);

  return (
    <div>
      <QueryState query={query} empty={(d) => d.trades.length === 0}>
        {(data) => (
          <div className="space-y-5">
            {/* hero + date selector */}
            <Card>
              <CardBody className="flex flex-wrap items-end justify-between gap-4">
                <div>
                  <div className="text-xs uppercase tracking-wide text-fg-muted">Book value (priced)</div>
                  <div
                    className={cn(
                      "tnum text-4xl font-semibold",
                      data.total_pv >= 0 ? "text-success-fg" : "text-danger-fg",
                    )}
                    title={exactUsd(data.total_pv)}
                  >
                    {humanizeUsd(data.total_pv)}
                  </div>
                  <div className="mt-1 text-xs text-fg-secondary">
                    {data.trades.length} trades · val {data.valuation_date}
                    {data.prior_date && ` · Δ vs ${data.prior_date}`}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex gap-2 text-xs">
                    <Zone status="green" label={`${data.counts.ok} ok`} />
                    {data.counts.blocked > 0 && <Zone status="amber" label={`${data.counts.blocked} blocked`} />}
                    {data.counts.failed > 0 && <Zone status="red" label={`${data.counts.failed} failed`} />}
                    <Zone status={data.counts.warnings ? "amber" : "green"} label={`${data.counts.warnings} warnings`} />
                  </div>
                  <select
                    className="rounded border border-strong px-2 py-1 text-xs"
                    value={data.valuation_date ?? ""}
                    onChange={(e) => setDate(e.target.value)}
                  >
                    {data.available_dates.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                </div>
              </CardBody>
            </Card>

            {/* by-asset-class strip (pre-filter for the grid) */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              {data.by_asset_class.map((a) => (
                <button
                  key={a.asset_class}
                  type="button"
                  onClick={() => setAcFilter(acFilter === a.asset_class ? null : a.asset_class)}
                  className={cn(
                    "rounded-lg border bg-surface-1 p-3 text-left shadow-sm transition-colors",
                    acFilter === a.asset_class ? "border-primary ring-1 ring-primary" : "border hover:border-strong",
                  )}
                >
                  <div className="text-xs font-medium capitalize text-fg-secondary">{a.asset_class}</div>
                  <div
                    className={cn("tnum text-base font-semibold", (a.total_pv ?? 0) >= 0 ? "text-fg" : "text-danger-fg")}
                    title={exactUsd(a.total_pv ?? 0)}
                  >
                    {humanizeUsd(a.total_pv)}
                  </div>
                  <div className="text-micro text-fg-muted">{a.n_trades} trades</div>
                </button>
              ))}
            </div>

            {/* trade grid */}
            <Card>
              <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
                <h3 className="text-sm font-semibold text-fg">
                  Trades {acFilter && <span className="font-normal text-fg-muted">· {acFilter}</span>}
                </h3>
                {acFilter && (
                  <button type="button" onClick={() => setAcFilter(null)} className="text-xs text-fg-secondary hover:text-fg">
                    clear filter
                  </button>
                )}
              </div>
              <DataTable
                columns={COLUMNS}
                rows={filteredTrades}
                getRowKey={(t) => t.trade_id}
                initialSort={{ key: "pv", dir: "desc" }}
                pageSize={25}
                exportName={`book_${data.valuation_date ?? "latest"}`}
              />
            </Card>
          </div>
        )}
      </QueryState>
    </div>
  );
}
