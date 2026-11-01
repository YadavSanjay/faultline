import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { EChartsOption } from "echarts";
import { useBook, useBookHistory, type BookHistoryResponse } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { EChart } from "@/components/shared/EChart";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// PR overview: the "5-second book" landing — value, period P&L, composition, top movers,
// and the book's PV trend. Every panel drills into the Book table or a single trade.

function TrendCard({ history }: { history: BookHistoryResponse }) {
  const [mode, setMode] = useState<"total" | "by_ac">("total");
  const { dates, total, series, asset_classes } = history;

  const option = useMemo<EChartsOption>(() => {
    const yFmt = (v: number) => humanizeUsd(v);
    if (mode === "total") {
      return {
        grid: { left: 64, right: 24, top: 16, bottom: 28 },
        tooltip: { trigger: "axis", valueFormatter: (v) => exactUsd(v as number) },
        xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
        yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, formatter: yFmt } },
        series: [{
          name: "Book PV", type: "line", smooth: true, symbolSize: 7,
          data: total.map((t) => t.total_pv), areaStyle: { opacity: 0.1 },
        }],
      };
    }
    return {
      grid: { left: 64, right: 24, top: 24, bottom: 28 },
      tooltip: { trigger: "axis", valueFormatter: (v) => humanizeUsd(v as number) },
      legend: { top: 0, data: asset_classes, textStyle: { fontSize: 10 } },
      xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
      yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, formatter: yFmt } },
      series: asset_classes.map((ac) => ({
        name: ac, type: "line", stack: "pv", smooth: true, showSymbol: false,
        areaStyle: { opacity: 0.28 }, lineStyle: { width: 1.5 }, data: series[ac] ?? [],
      })),
    };
  }, [mode, dates, total, series, asset_classes]);

  return (
    <Card>
      <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
        <h3 className="text-sm font-semibold text-fg">Book PV trend</h3>
        <div className="flex gap-1 text-xs">
          {(["total", "by_ac"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded px-2 py-0.5",
                mode === m ? "bg-primary text-primary-fg" : "text-fg-secondary hover:bg-surface-0",
              )}
            >
              {m === "total" ? "Total" : "By asset class"}
            </button>
          ))}
        </div>
      </div>
      <CardBody>
        {dates.length > 1 ? (
          <EChart option={option} height={280} />
        ) : (
          <div className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-secondary">
            Only one valuation date stored so far — the trend grows as more runs land.
          </div>
        )}
      </CardBody>
    </Card>
  );
}

export function PricingOverview() {
  const bookQuery = useBook(undefined);
  const historyQuery = useBookHistory();

  return (
    <QueryState query={bookQuery} empty={(d) => d.trades.length === 0}>
      {(book) => {
        const gross = book.by_asset_class.reduce((s, a) => s + Math.abs(a.total_pv ?? 0), 0) || 1;
        const maxAbs = Math.max(1, ...book.by_asset_class.map((a) => Math.abs(a.total_pv ?? 0)));
        const movers = [...book.trades]
          .filter((t) => t.delta != null)
          .sort((a, b) => Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))
          .slice(0, 6);
        const latestPnl = historyQuery.data?.total.at(-1)?.pnl ?? null;

        const metrics: Metric[] = [
          {
            label: "Book value",
            value: humanizeUsd(book.total_pv),
            title: exactUsd(book.total_pv),
            tone: book.total_pv >= 0 ? "gain" : "loss",
            caption: `val ${book.valuation_date}`,
          },
          {
            label: "Period P&L",
            value: latestPnl == null ? "—" : humanizeUsd(latestPnl),
            title: latestPnl == null ? undefined : exactUsd(latestPnl),
            tone: latestPnl == null ? "neutral" : latestPnl >= 0 ? "gain" : "loss",
            caption: book.prior_date ? `vs ${book.prior_date}` : "first stored date",
          },
          { label: "Trades", value: book.trades.length, caption: `${book.by_asset_class.length} asset classes` },
          {
            label: "Priced OK",
            value: `${book.counts.ok}/${book.trades.length}`,
            tone: book.counts.failed ? "loss" : "neutral",
            caption: `${book.counts.warnings} warnings · ${book.counts.blocked} blocked · ${book.counts.failed} failed`,
          },
        ];

        return (
          <div className="space-y-5">
            <Card>
              <CardBody>
                <MetricGrid metrics={metrics} columns={4} />
              </CardBody>
            </Card>

            {historyQuery.data && <TrendCard history={historyQuery.data} />}

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* composition */}
              <Card>
                <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
                  <h3 className="text-sm font-semibold text-fg">Composition by asset class</h3>
                  <Link to="/pricing/book" className="text-xs text-fg-secondary hover:text-fg">
                    full book ▸
                  </Link>
                </div>
                <CardBody className="space-y-2">
                  {[...book.by_asset_class]
                    .sort((a, b) => Math.abs(b.total_pv ?? 0) - Math.abs(a.total_pv ?? 0))
                    .map((a) => (
                      <div key={a.asset_class} className="flex items-center gap-3">
                        <div className="w-24 shrink-0 truncate text-xs capitalize text-fg-secondary">
                          {a.asset_class}
                        </div>
                        <div className="h-3 flex-1 overflow-hidden rounded bg-surface-0">
                          <div
                            className={cn("h-full rounded", (a.total_pv ?? 0) >= 0 ? "bg-success" : "bg-danger")}
                            style={{ width: `${(Math.abs(a.total_pv ?? 0) / maxAbs) * 100}%` }}
                          />
                        </div>
                        <div
                          className={cn("tnum w-20 shrink-0 text-right text-xs", (a.total_pv ?? 0) >= 0 ? "text-fg" : "text-danger-fg")}
                          title={exactUsd(a.total_pv ?? 0)}
                        >
                          {humanizeUsd(a.total_pv)}
                        </div>
                        <div className="w-10 shrink-0 text-right text-micro text-fg-muted">
                          {Math.round((Math.abs(a.total_pv ?? 0) / gross) * 100)}%
                        </div>
                      </div>
                    ))}
                </CardBody>
              </Card>

              {/* top movers */}
              <Card>
                <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
                  <h3 className="text-sm font-semibold text-fg">
                    Top movers {book.prior_date && <span className="font-normal text-fg-muted">· vs {book.prior_date}</span>}
                  </h3>
                </div>
                <CardBody>
                  {movers.length === 0 ? (
                    <div className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-secondary">
                      No prior date to compare — movers appear once a second run lands.
                    </div>
                  ) : (
                    <table className="w-full text-sm">
                      <tbody className="divide-y divide-border-subtle">
                        {movers.map((t) => {
                          const up = (t.delta ?? 0) >= 0;
                          return (
                            <tr key={t.trade_id} className="hover:bg-surface-2">
                              <td className="py-1.5 pr-2">
                                <Link to={`/pricing/trade/${t.trade_id}`} className="font-mono text-xs text-fg hover:underline">
                                  {t.trade_id}
                                </Link>
                                <div className="max-w-[16rem] truncate text-micro text-fg-muted" title={t.description}>
                                  {t.description}
                                </div>
                              </td>
                              <td className={cn("py-1.5 text-right tnum", up ? "text-success-fg" : "text-danger-fg")} title={exactUsd(t.delta ?? 0)}>
                                {up ? "▲" : "▼"} {humanizeUsd(Math.abs(t.delta ?? 0))}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </CardBody>
              </Card>
            </div>

            <div className="flex items-center gap-2 text-xs text-fg-secondary">
              <Zone status={book.counts.failed ? "red" : book.counts.blocked || book.counts.warnings ? "amber" : "green"} label="pricing status" />
              <span>
                Drill down: <Link to="/pricing/book" className="text-fg-secondary underline">book table</Link> → a trade → calculation lineage.
              </span>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}
