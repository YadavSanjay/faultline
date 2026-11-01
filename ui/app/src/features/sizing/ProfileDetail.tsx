import { Link, useParams } from "react-router-dom";
import { useSizingProfile } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";
import { themeFor, pct, signedPct, directionTag, multiplierTone } from "./format";

export function ProfileDetail() {
  const { profile = "balanced" } = useParams();
  const query = useSizingProfile(profile);
  const theme = themeFor(profile);

  return (
    <div>
      <div className="mb-4 flex items-center gap-2 text-sm">
        <Link to="/sizing" className="text-fg-secondary hover:text-fg">
          Portfolio Construction
        </Link>
        <span className="text-fg-muted">/</span>
        <span className={cn("font-semibold", theme.accent)}>{theme.label}</span>
      </div>

      <QueryState query={query} empty={(d) => !d.available}>
        {(d) => {
          const retTone: Metric["tone"] = d.expected_annual_return >= 0 ? "gain" : "loss";
          const metrics: Metric[] = [
            {
              label: "1-day 99% VaR",
              value: humanizeUsd(d.achieved_var99),
              title: exactUsd(d.achieved_var99),
              caption: `budget ${humanizeUsd(d.var_budget)} · binds on ${d.binding_constraint.replace(/_/g, " ")}`,
            },
            {
              label: "Economic capital (ES 97.5%)",
              value: humanizeUsd(d.achieved_es975),
              title: exactUsd(d.achieved_es975),
              caption: "FRTB tail measure",
            },
            {
              label: "Gross leverage",
              value: `${d.gross_leverage.toFixed(2)}×`,
              caption: `net ${humanizeUsd(d.net_notional)}`,
            },
            {
              label: "Expected return",
              value: signedPct(d.expected_return_pct),
              tone: retTone,
              caption: `${humanizeUsd(d.expected_annual_return)} / yr`,
            },
            {
              label: "Return / VaR",
              value: `${d.return_on_var >= 0 ? "+" : ""}${d.return_on_var.toFixed(2)}`,
              tone: retTone,
              caption: "risk-adjusted",
            },
            {
              label: "Annualised vol",
              value: humanizeUsd(d.portfolio_vol_annual),
              title: exactUsd(d.portfolio_vol_annual),
              caption: "√252 × daily",
            },
          ];

          const maxRc = Math.max(...d.sizes.map((s) => Math.abs(s.risk_contribution)), 1);
          const stress = d.revaluation?.stress_pnl ?? {};
          const maxStress = Math.max(...Object.values(stress).map((v) => Math.abs(v)), 1);

          return (
            <div className="space-y-5">
              <div className="flex items-center justify-between">
                <p className="max-w-2xl text-sm text-fg-secondary">{theme.blurb}</p>
                <ProvenanceBadge items={{ run_id: d.run_id }} />
              </div>

              <MetricGrid metrics={metrics} columns={3} />

              {/* stress breakdown */}
              {Object.keys(stress).length > 0 && (
                <Card>
                  <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                    Stress P&amp;L under this configuration
                  </div>
                  <CardBody className="space-y-2">
                    {Object.entries(stress)
                      .sort((a, b) => a[1] - b[1])
                      .map(([name, v]) => (
                        <div key={name} className="flex items-center gap-3">
                          <div className="w-44 truncate text-xs text-fg-secondary" title={name}>{name}</div>
                          <div className="relative h-4 flex-1 rounded bg-surface-0">
                            <div
                              className={cn("absolute top-0 h-4 rounded", v >= 0 ? "bg-success" : "bg-danger")}
                              style={{ width: `${(Math.abs(v) / maxStress) * 100}%` }}
                            />
                          </div>
                          <div
                            className={cn("tnum w-24 text-right text-xs font-semibold", v >= 0 ? "text-success-fg" : "text-danger-fg")}
                            title={exactUsd(v)}
                          >
                            {humanizeUsd(v)}
                          </div>
                        </div>
                      ))}
                  </CardBody>
                </Card>
              )}

              {/* per-trade sizes */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Position sizes &amp; risk contributions
                </div>
                <CardBody className="overflow-x-auto">
                  <table className="w-full min-w-[760px] text-body-sm">
                    <thead>
                      <tr className="text-micro uppercase tracking-wide text-fg-muted">
                        <th className="py-1.5 pr-2 text-left font-medium">Position</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Target</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Size</th>
                        <th className="py-1.5 pr-3 text-left font-medium">Risk contribution</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Exp. return</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle">
                      {d.sizes.map((s) => {
                        const dir = directionTag(s);
                        const rcPos = s.risk_contribution >= 0;
                        return (
                          <tr key={s.trade_id} className="hover:bg-surface-2">
                            <td className="py-2 pr-2">
                              <div className="flex items-center gap-2">
                                <span className={cn("rounded px-1 py-0.5 text-nano font-bold tracking-wide", dir.cls)}>
                                  {dir.label}
                                </span>
                                <span className="truncate text-fg-secondary" style={{ maxWidth: 260 }}>{s.description}</span>
                              </div>
                            </td>
                            <td className="tnum py-2 pr-3 text-right font-medium text-fg" title={exactUsd(s.target_notional)}>
                              {humanizeUsd(s.target_notional)}
                            </td>
                            <td className={cn("tnum py-2 pr-3 text-right text-xs", multiplierTone(s.multiplier, s.is_hedge))}>
                              {s.multiplier.toFixed(2)}×
                            </td>
                            <td className="py-2 pr-3">
                              <div className="flex items-center gap-2">
                                <div className="relative h-3 w-28 overflow-hidden rounded bg-surface-0">
                                  <div
                                    className={cn("absolute top-0 h-3", rcPos ? "left-1/2 bg-surface-20" : "right-1/2 bg-warning")}
                                    style={{ width: `${(Math.abs(s.risk_contribution) / maxRc) * 50}%` }}
                                  />
                                  <div className="absolute left-1/2 top-0 h-3 w-px bg-border-strong" />
                                </div>
                                <span className={cn("tnum text-micro", rcPos ? "text-fg-secondary" : "text-warning-fg")}>
                                  {pct(s.risk_share)}
                                </span>
                              </div>
                            </td>
                            <td
                              className={cn("tnum py-2 pr-3 text-right text-xs", s.mu_annual >= 0 ? "text-success-fg" : "text-danger-fg")}
                              title={exactUsd(s.mu_annual)}
                            >
                              {humanizeUsd(s.mu_annual)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <div className="mt-2 flex flex-wrap gap-4 text-micro text-fg-muted">
                    <span>Risk contribution: grey = adds risk, amber (left) = reduces risk (hedge / offset)</span>
                    <span>% = signed share of portfolio volatility</span>
                  </div>
                </CardBody>
              </Card>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
