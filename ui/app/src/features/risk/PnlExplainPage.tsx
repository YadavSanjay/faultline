import { useRiskReport } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

interface PnlTradeRow {
  trade_id: string;
  asset_class: string;
  actual: number;
  explained_total: number;
  residual: number;
}

const PNL_COLUMNS: DataColumn<PnlTradeRow>[] = [
  { key: "trade_id", header: "Trade", get: (t) => t.trade_id, filterable: true, render: (t) => <span className="font-mono text-fg">{t.trade_id}</span> },
  { key: "asset_class", header: "Asset class", get: (t) => t.asset_class, filterable: true, render: (t) => <span className="capitalize text-fg-secondary">{t.asset_class}</span> },
  { key: "actual", header: "Actual", align: "right", get: (t) => t.actual, csv: (t) => t.actual, render: (t) => <span className="text-fg">{humanizeUsd(t.actual)}</span> },
  { key: "explained_total", header: "Explained", align: "right", get: (t) => t.explained_total, csv: (t) => t.explained_total, render: (t) => <span className="text-fg-secondary">{humanizeUsd(t.explained_total)}</span> },
  {
    key: "residual", header: "Residual", align: "right", get: (t) => t.residual, csv: (t) => t.residual,
    render: (t) => (
      <span className={cn(Math.abs(t.residual) > Math.abs(t.actual) * 0.3 ? "font-medium text-warning-fg" : "text-fg-secondary")} title={exactUsd(t.residual)}>
        {humanizeUsd(t.residual)}
      </span>
    ),
  },
];

// RK-B4 P&L Explain: actual (full-reval) vs explained (Greeks × shocks) vs residual, with
// coverage — validates that first-order Greeks capture the trade's risk.
export function PnlExplainPage() {
  const query = useRiskReport();
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(report) => {
        const carry = report.carry_theta;
        const pe = report.pnl_explain;
        const carryCard = carry && (
          <Card>
            <CardHeader title="Carry / theta" subtitle={`predicted ${carry.horizon_days}-day P&L from time passing, market held flat`} />
            <CardBody>
              <div className="mb-2 flex items-end gap-2">
                <span className={cn("tnum text-2xl font-semibold", carry.total_carry >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(carry.total_carry)}>
                  {humanizeUsd(carry.total_carry)}
                </span>
                <span className="text-xs text-fg-muted">total book carry</span>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {Object.entries(carry.carry_by_asset_class)
                  .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                  .map(([ac, val]) => (
                    <span key={ac} className="text-xs">
                      <span className="capitalize text-fg-secondary">{ac}</span>{" "}
                      <span className={cn("tnum", val >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(val)}>{humanizeUsd(val)}</span>
                    </span>
                  ))}
              </div>
              <div className="mt-2 text-micro text-fg-muted">
                Long options decay (negative theta); bonds and receivers earn carry — the time-decay leg a full
                attribution needs beyond the market-move Greeks.
              </div>
            </CardBody>
          </Card>
        );

        if (!pe) {
          return (
            <div className="space-y-4">
              {carryCard}
              <WarningBanner tone="info" title="No scenario P&L explain in this run">
                Run the risk batch with <code>--explain &lt;scenario&gt;</code> to attribute a scenario's P&amp;L to Greeks.
              </WarningBanner>
            </div>
          );
        }
        const factors = Object.entries(pe.explained_by_factor).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
        const maxAbs = Math.max(...factors.map(([, v]) => Math.abs(v)), Math.abs(pe.actual_total), 1);
        const coveragePct = Math.round(pe.coverage * 100);

        return (
          <div className="space-y-4">
            {carryCard}
            <Card>
              <CardHeader title={`P&L attribution — ${pe.scenario}`} subtitle="risk-theoretical (Greeks × shock) vs hypothetical (full revaluation)" />
              <CardBody className="space-y-4">
                <div className="flex flex-wrap items-end gap-8">
                  <div>
                    <div className="text-xs uppercase text-fg-muted">Actual P&L</div>
                    <div className={cn("tnum text-2xl font-semibold", pe.actual_total >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(pe.actual_total)}>
                      {humanizeUsd(pe.actual_total)}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs uppercase text-fg-muted">Residual (unexplained)</div>
                    <div className="tnum text-2xl font-semibold text-fg" title={exactUsd(pe.residual_total)}>
                      {humanizeUsd(pe.residual_total)}
                    </div>
                  </div>
                  <div className="min-w-[200px] flex-1">
                    <div className="mb-1 flex justify-between text-xs text-fg-secondary">
                      <span>coverage by first-order Greeks</span>
                      <span className="font-medium">{coveragePct}%</span>
                    </div>
                    <div className="h-3 w-full overflow-hidden rounded-full bg-surface-0">
                      <div className={cn("h-3 rounded-full", coveragePct >= 90 ? "bg-success" : coveragePct >= 70 ? "bg-warning" : "bg-danger")} style={{ width: `${Math.min(100, coveragePct)}%` }} />
                    </div>
                  </div>
                </div>

                <div>
                  <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">Explained by factor</div>
                  <div className="space-y-1.5">
                    {factors.map(([f, v]) => (
                      <div key={f} className="flex items-center gap-3">
                        <span className="w-28 shrink-0 text-xs text-fg-secondary">{f}</span>
                        <div className="relative h-4 flex-1 rounded bg-surface-0">
                          <div className={cn("absolute top-0 h-4 rounded", v >= 0 ? "bg-success" : "bg-danger")} style={{ width: `${(Math.abs(v) / maxAbs) * 100}%` }} />
                        </div>
                        <span className={cn("tnum w-24 shrink-0 text-right text-xs", v >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(v)}>
                          {humanizeUsd(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Per-trade attribution" />
              <DataTable
                columns={PNL_COLUMNS}
                rows={[...pe.trades].sort((a, b) => Math.abs(b.actual) - Math.abs(a.actual))}
                getRowKey={(t) => t.trade_id}
                pageSize={30}
                exportName={`pnl_explain_${pe.scenario}`}
              />
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}
