import { Link } from "react-router-dom";
import { useSizing, type SizingProfile, type SizingSize } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PROFILE_ORDER, themeFor, signedPct, directionTag, multiplierTone } from "./format";

function ProfileCard({ p }: { p: SizingProfile }) {
  const t = themeFor(p.profile);
  const retTone = p.expected_annual_return >= 0 ? "text-success-fg" : "text-danger-fg";
  return (
    <Link
      to={`/sizing/${p.profile}`}
      className={cn(
        "block rounded-lg border border-t-[3px] border bg-surface-1 p-4 shadow-sm hover:shadow-md",
        t.ring,
      )}
    >
      <div className="flex items-baseline justify-between">
        <span className={cn("text-xs font-semibold uppercase tracking-wide", t.accent)}>{t.label}</span>
        <span className="text-micro text-fg-muted">{p.binding_constraint.replace(/_/g, " ")}</span>
      </div>
      <div className="mt-1 tnum text-3xl font-semibold text-fg" title={exactUsd(p.achieved_var99)}>
        {humanizeUsd(p.achieved_var99)}
      </div>
      <div className="text-micro text-fg-muted">1-day 99% VaR · budget {humanizeUsd(p.var_budget)}</div>
      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <div>
          <dt className="text-nano uppercase tracking-wide text-fg-muted">Gross leverage</dt>
          <dd className="tnum font-semibold text-fg">{p.gross_leverage.toFixed(2)}×</dd>
        </div>
        <div>
          <dt className="text-nano uppercase tracking-wide text-fg-muted">Expected return</dt>
          <dd className={cn("tnum font-semibold", retTone)}>{signedPct(p.expected_return_pct)}</dd>
        </div>
        <div>
          <dt className="text-nano uppercase tracking-wide text-fg-muted">Return / VaR</dt>
          <dd className={cn("tnum font-semibold", retTone)}>{p.return_on_var >= 0 ? "+" : ""}{p.return_on_var.toFixed(2)}</dd>
        </div>
        <div>
          <dt className="text-nano uppercase tracking-wide text-fg-muted">ES 97.5%</dt>
          <dd className="tnum font-semibold text-fg" title={exactUsd(p.achieved_es975)}>{humanizeUsd(p.achieved_es975)}</dd>
        </div>
      </dl>
      <div className={cn("mt-3 rounded px-2 py-1 text-micro", t.soft, t.accent)}>{themeFor(p.profile).blurb}</div>
    </Link>
  );
}

export function SizingOverview() {
  const query = useSizing();
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(data) => {
        const profiles = PROFILE_ORDER.map((k) => data.profiles[k]).filter(Boolean) as SizingProfile[];
        const base = profiles[0]?.sizes ?? [];
        const reval = data.revaluation ?? {};
        const revalCols = ["current", ...PROFILE_ORDER];

        // index sizes[trade][profile]
        const sizeOf = (tid: string, prof: string): SizingSize | undefined =>
          data.profiles[prof]?.sizes.find((s) => s.trade_id === tid);

        const compareColumns: DataColumn<SizingSize>[] = [
          {
            key: "position",
            header: "Position",
            get: (s) => s.trade_id,
            filterable: true,
            csv: (s) => s.trade_id,
            render: (s0) => {
              const dir = directionTag(s0);
              return (
                <div className="flex items-center gap-2">
                  <span className={cn("rounded px-1 py-0.5 text-nano font-bold tracking-wide", dir.cls)}>{dir.label}</span>
                  <span className="font-mono text-micro text-fg-muted">{s0.trade_id.replace("TRADE_", "#")}</span>
                  <span className="truncate text-fg-secondary" style={{ maxWidth: 230 }} title={s0.description}>{s0.description}</span>
                </div>
              );
            },
          },
          {
            key: "now",
            header: "Now",
            align: "right",
            get: (s) => s.current_notional,
            csv: (s) => s.current_notional,
            render: (s) => <span className="text-fg-muted" title={exactUsd(s.current_notional)}>{humanizeUsd(s.current_notional)}</span>,
          },
          ...PROFILE_ORDER.map<DataColumn<SizingSize>>((k) => ({
            key: `p_${k}`,
            header: themeFor(k).label.split(" ")[0],
            align: "right",
            headerClassName: themeFor(k).accent,
            get: (s0) => sizeOf(s0.trade_id, k)?.target_notional ?? null,
            csv: (s0) => sizeOf(s0.trade_id, k)?.target_notional ?? "",
            render: (s0) => {
              const s = sizeOf(s0.trade_id, k);
              if (!s) return <span className="text-fg-muted">—</span>;
              return (
                <div>
                  <div className="font-medium text-fg" title={exactUsd(s.target_notional)}>{humanizeUsd(s.target_notional)}</div>
                  <div className={cn("text-nano", multiplierTone(s.multiplier, s.is_hedge))}>{s.multiplier.toFixed(2)}×</div>
                </div>
              );
            },
          })),
        ];

        return (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-xs text-fg-secondary">
                val {data.valuation_date} · capital base {humanizeUsd(data.capital_base)} · {data.n_days}d ·{" "}
                {data.n_factors} factors · {data.n_trades} trades
              </div>
              <ProvenanceBadge items={{ run_id: data.run_id }} />
            </div>

            {/* three profile cards */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {profiles.map((p) => (
                <ProfileCard key={p.profile} p={p} />
              ))}
            </div>

            {/* PV / Risk / Capital re-valuation */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                Re-valuation — PV, Risk &amp; Capital per configuration
              </div>
              <CardBody className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead>
                    <tr className="text-left text-micro uppercase tracking-wide text-fg-muted">
                      <th className="py-1.5 pr-3 font-medium">Configuration</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Book PV</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Gross notional</th>
                      <th className="py-1.5 pr-3 text-right font-medium">VaR 99%</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Econ. capital (ES 97.5%)</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Worst stress loss</th>
                      <th className="py-1.5 font-medium">Worst scenario</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {revalCols.map((k) => {
                      const r = reval[k];
                      if (!r) return null;
                      const isCur = k === "current";
                      return (
                        <tr key={k} className={cn(isCur && "text-fg-secondary")}>
                          <td className="py-2 pr-3 font-medium">
                            {isCur ? (
                              <span>Current book</span>
                            ) : (
                              <Link to={`/sizing/${k}`} className={cn("hover:underline", themeFor(k).accent)}>
                                {r.label}
                              </Link>
                            )}
                          </td>
                          <td className="tnum py-2 pr-3 text-right" title={exactUsd(r.total_pv)}>{humanizeUsd(r.total_pv)}</td>
                          <td className="tnum py-2 pr-3 text-right" title={exactUsd(r.gross_notional)}>{humanizeUsd(r.gross_notional)}</td>
                          <td className="tnum py-2 pr-3 text-right" title={exactUsd(r.var99)}>{humanizeUsd(r.var99)}</td>
                          <td className="tnum py-2 pr-3 text-right" title={exactUsd(r.es975)}>{humanizeUsd(r.es975)}</td>
                          <td className="tnum py-2 pr-3 text-right text-danger-fg" title={exactUsd(-r.worst_stress_loss)}>
                            −{humanizeUsd(r.worst_stress_loss)}
                          </td>
                          <td className="py-2 text-fg-secondary">{r.worst_stress_scenario}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="mt-2 text-micro text-fg-muted">
                  Position scaling is exactly linear for a fixed market state, so PV and stress P&amp;L are the
                  per-trade values reweighted by each profile's multipliers. Note the worst-case scenario shifts
                  from a credit shock (hedged balanced book) to an AI-capex bust (concentrated, hedge-light books).
                </p>
              </CardBody>
            </Card>

            {/* master per-trade comparison */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                Every position, every mandate
              </div>
              <DataTable
                columns={compareColumns}
                rows={base}
                getRowKey={(s) => s.trade_id}
                pageSize={30}
                exportName={`sizing_comparison_${data.valuation_date ?? "latest"}`}
              />
              <div className="flex flex-wrap gap-4 px-4 py-2 text-micro text-fg-muted">
                <span>×n = size vs current</span>
                <span>SHORT = directional short (SMH is the semis RV leg)</span>
                <span>HEDGE sized by appetite</span>
                <span>open a profile for risk contributions &amp; expected return</span>
              </div>
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}
