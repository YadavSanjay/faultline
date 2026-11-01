import { useState } from "react";
import { Link } from "react-router-dom";
import { useRiskReport, type RiskReport, type JtdIssuer } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { classLabel, factorCaption, HEADLINE_FACTORS, lossText } from "./format";
import { cn } from "@/lib/cn";

type JtdRow = JtdIssuer & { isIndex: boolean };

const JTD_COLUMNS: DataColumn<JtdRow>[] = [
  { key: "issuer", header: "Issuer", get: (i) => i.issuer, filterable: true, render: (i) => <span className="font-mono text-xs">{i.issuer}</span> },
  {
    key: "bucket", header: "Rating", get: (i) => i.bucket, filterable: true,
    render: (i) => (i.isIndex ? <span className="text-xs">index<span className="text-nano"> · diversified</span></span> : <span className="text-xs">{i.bucket}</span>),
  },
  { key: "gross_notional", header: "Gross notional", align: "right", get: (i) => i.gross_notional, csv: (i) => i.gross_notional, render: (i) => <span title={exactUsd(i.gross_notional)}>{humanizeUsd(i.gross_notional)}</span> },
  { key: "lgd", header: "LGD", align: "right", get: (i) => i.lgd, csv: (i) => i.lgd, render: (i) => <span>{(i.lgd * 100).toFixed(0)}%</span> },
  {
    key: "jtd", header: "JTD P&L", align: "right", get: (i) => i.jtd, csv: (i) => i.jtd,
    render: (i) => (
      <span className={cn(!i.isIndex && "font-medium", i.jtd >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(i.jtd)}>
        {i.jtd >= 0 ? humanizeUsd(i.jtd) : lossText(i.jtd)}
      </span>
    ),
  },
];

function levels(v: Record<string, number>): string[] {
  return Object.keys(v).sort((a, b) => Number(a) - Number(b));
}

export function RiskHeader({ report }: { report: RiskReport }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div className="text-xs text-fg-secondary">val {report.valuation_date}</div>
      <ProvenanceBadge
        items={{
          run_id: report.run_id,
          snapshot_id: report.snapshot_id,
          factor_snapshot_id: report.factor_snapshot_id,
        }}
      />
    </div>
  );
}

function SensRow({ factor, value }: { factor: string; value: number }) {
  const tone = value > 0 ? "text-success-fg" : value < 0 ? "text-danger-fg" : "text-fg";
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <div className="min-w-0">
        <div className="font-mono text-xs text-fg">{factor}</div>
        <div className="truncate text-micro text-fg-muted">{factorCaption(factor)}</div>
      </div>
      <div className={cn("tnum shrink-0 text-sm font-medium", tone)} title={exactUsd(value)}>
        {humanizeUsd(value)}
      </div>
    </div>
  );
}

export function RiskDashboard() {
  const query = useRiskReport();
  const [showAll, setShowAll] = useState(false);

  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(report) => {
        const bs = report.book_sensitivities ?? {};
        const headline = HEADLINE_FACTORS.filter((f) => f in bs);
        const allFactors = Object.entries(bs).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
        const v = report.var;
        const fr = report.full_reval_var;
        // Full-revaluation VaR is the headline (captures gamma/vega convexity exactly); the
        // sensitivity-based delta-gamma VaR is kept as the fast first-order benchmark. (Tier 1.2)
        const headlineVar = fr ?? v ?? null;
        const headlineLabel = fr ? "full revaluation" : "sensitivity Δγ";
        const decomp = v?.decomposition?.by_level?.["0.99"];
        const fhs = v?.fhs;
        const stressed = v?.stressed;
        const param = v?.parametric;
        // VaR "family" at 99% — the same book seen five ways (Tier 1.2 / 2.4 / 2.5 / 3.7).
        const family = [
          fr && { label: "Full revaluation", sub: "exact gamma/vega", varv: fr.var["0.99"], es: fr.es["0.99"] },
          v && { label: "Sensitivity Δγ", sub: "equal-weighted HS", varv: v.var["0.99"], es: v.es["0.99"] },
          fhs && { label: "Filtered HS", sub: `EWMA λ=${fhs.lambda}`, varv: fhs.var["0.99"], es: fhs.es["0.99"] },
          stressed && { label: "Stressed", sub: `${stressed.window_start} → ${stressed.window_end}`, varv: stressed.var["0.99"], es: stressed.es["0.99"] },
          param && { label: "Parametric", sub: "variance-covariance (Gaussian)", varv: param.var["0.99"], es: param.es["0.99"] },
        ].filter(Boolean) as { label: string; sub: string; varv: number; es: number }[];
        const jtd = report.jtd;
        const carry = report.carry_theta;
        const worstScenarios = [...(report.scenarios ?? [])].sort((a, b) => a.total_pnl - b.total_pnl);
        const bt = report.backtest;
        const btFr = report.backtest_full_reval;

        return (
          <div>
            <RiskHeader report={report} />

            {v && v.missing_factors.length > 0 && (
              <div className="mb-4">
                <WarningBanner tone="error" title={`VaR understated — ${v.missing_factors.length} factor(s) have exposure but no history`}>
                  {v.missing_factors.join(", ")}
                </WarningBanner>
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {/* VaR / ES — full-revaluation headline, linear Δγ benchmark */}
              <Card>
                <CardHeader
                  title="VaR / Expected Shortfall"
                  subtitle={headlineVar ?`${headlineLabel} · ${headlineVar.lookback_days}d lookback · ${headlineVar.horizon_days}d horizon · ${headlineVar.n_scenarios} days` : ""}
                />
                <CardBody>
                  {headlineVar ?(
                    <>
                      <table className="w-full text-sm">
                        <thead className="text-left text-xs text-fg-muted">
                          <tr>
                            <th className="py-1 font-medium">Level</th>
                            <th className="py-1 text-right font-medium">VaR</th>
                            <th className="py-1 text-right font-medium">ES</th>
                            {v && fr && <th className="py-1 text-right font-medium text-fg-muted">Δγ VaR</th>}
                          </tr>
                        </thead>
                        <tbody>
                          {levels(headlineVar.var).map((lvl) => (
                            <tr key={lvl} className={lvl === "0.975" ? "bg-warning-tint/40" : ""}>
                              <td className="py-1 tnum text-fg-secondary">
                                {(Number(lvl) * 100).toFixed(1)}%{lvl === "0.975" && <span className="ml-1 rounded bg-warning-tint px-1 text-nano text-warning-fg">FRTB</span>}
                              </td>
                              <td className="py-1 text-right tnum font-medium text-danger-fg" title={exactUsd(headlineVar.var[lvl])}>
                                {lossText(headlineVar.var[lvl])}
                              </td>
                              <td className="py-1 text-right tnum text-danger-fg" title={exactUsd(headlineVar.es[lvl])}>
                                {lossText(headlineVar.es[lvl])}
                              </td>
                              {v && fr && (
                                <td className="py-1 text-right tnum text-fg-muted" title={exactUsd(v.var[lvl])}>
                                  {lossText(v.var[lvl])}
                                </td>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div className="mt-2 text-xs text-fg-secondary">
                        On a 1-in-{Math.round(1 / (1 - 0.99))} day we'd expect to lose about{" "}
                        <span className="font-medium text-danger-fg">{lossText(headlineVar.var["0.99"])}</span> or more.
                      </div>
                      {v && fr && (
                        <div className="mt-1 text-micro text-fg-muted">
                          Headline is full revaluation (exact gamma/vega). <span className="font-mono">Δγ VaR</span> is the
                          first-order sensitivity benchmark — closeness is the P&L-attribution coverage check.
                        </div>
                      )}
                      {fr?.worst_day && (
                        <div className="mt-1 text-micro text-fg-muted">
                          Worst historical day: {fr.worst_day} ({lossText(fr.worst_pnl)}).
                        </div>
                      )}
                      <Link to="/risk/methodology" className="mt-2 inline-block text-xs text-fg-secondary hover:text-fg">
                        how it's computed ▸
                      </Link>
                    </>
                  ) : (
                    <div className="text-sm text-fg-muted">no VaR in this run</div>
                  )}
                </CardBody>
              </Card>

              {/* Book sensitivities */}
              <Card>
                <CardHeader title="Book sensitivities" subtitle="aggregated across the book" right={<Link to="/risk/drill" className="text-xs text-fg-secondary hover:text-fg">drill ▸</Link>} />
                <CardBody>
                  <div className="divide-y divide-border-subtle">
                    {(showAll ? allFactors.map(([f]) => f) : headline).map((f) => (
                      <SensRow key={f} factor={f} value={bs[f]} />
                    ))}
                  </div>
                  {allFactors.length > headline.length && (
                    <button type="button" onClick={() => setShowAll(!showAll)} className="mt-2 text-xs text-fg-secondary hover:text-fg">
                      {showAll ? "show headline" : `show all ${allFactors.length} factors`}
                    </button>
                  )}
                </CardBody>
              </Card>

              {/* Controls */}
              <Card>
                <CardHeader title="Controls" subtitle="model validation & mandate" />
                <CardBody className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-fg-secondary">Backtest <span className="text-nano text-fg-muted">Δγ</span></span>
                    {bt ? (
                      <Zone status={bt.zone as ZoneStatus} label={`${bt.zone} · ${bt.n_exceptions}/${bt.n_days}`} />
                    ) : (
                      <Zone status="unknown" label="not run" />
                    )}
                  </div>
                  {btFr && (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-fg-secondary">Backtest <span className="text-nano text-fg-muted">full-reval</span></span>
                      <Zone status={btFr.zone as ZoneStatus} label={`${btFr.zone} · ${btFr.n_exceptions}/${btFr.n_days}`} />
                    </div>
                  )}
                  {bt && (
                    <div className="flex items-center justify-between text-micro text-fg-muted">
                      <span>coverage tests (LR)</span>
                      <span className="tnum">
                        Kupiec {bt.kupiec_lr.toFixed(2)} · Christoffersen {(bt.christoffersen_lr ?? 0).toFixed(2)} · CC {(bt.cc_lr ?? 0).toFixed(2)}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-fg-secondary">Limits</span>
                    <Zone
                      status={(report.limit_breaches?.length ?? 0) > 0 ? "red" : "green"}
                      label={`${report.limit_breaches?.length ?? 0} breaches`}
                    />
                  </div>
                  {report.pnl_explain && (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-fg-secondary">P&L explain</span>
                      <Link to="/risk/pnl-explain" className="text-xs tnum text-fg hover:underline">
                        {(report.pnl_explain.coverage * 100).toFixed(0)}% coverage ▸
                      </Link>
                    </div>
                  )}
                  {carry && (
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-fg-secondary">Carry / theta <span className="text-nano text-fg-muted">{carry.horizon_days}d</span></span>
                      <Link to="/risk/pnl-explain" className={cn("text-xs tnum hover:underline", carry.total_carry >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(carry.total_carry)}>
                        {humanizeUsd(carry.total_carry)} ▸
                      </Link>
                    </div>
                  )}
                </CardBody>
              </Card>
            </div>

            {/* VaR family — the same book, four methodologies at 99% (Tier 1.2 / 2.4 / 2.5) */}
            {family.length > 1 && (
              <Card className="mt-4">
                <CardHeader
                  title="VaR family @ 99%"
                  subtitle="one book, four lenses — full-reval headline, first-order benchmark, vol-scaled (regime-aware) and stressed"
                />
                <CardBody>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                    {family.map((m) => (
                      <div key={m.label} className="rounded-md border border-subtle p-2">
                        <div className="text-xs font-medium text-fg-secondary">{m.label}</div>
                        <div className="truncate text-nano text-fg-muted" title={m.sub}>{m.sub}</div>
                        <div className="mt-1 tnum text-base font-semibold text-danger-fg" title={exactUsd(m.varv)}>{lossText(m.varv)}</div>
                        <div className="tnum text-micro text-danger-fg" title={exactUsd(m.es)}>ES {lossText(m.es)}</div>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 text-micro text-fg-muted">
                    Filtered HS rescales history to today's volatility (regime-aware); Stressed is the worst 250-day
                    sub-window in the sample — both address the short, benign post-2022 window.
                  </div>
                </CardBody>
              </Card>
            )}

            {/* Risk allocation — standalone vs diversified, component ES by factor class (Tier 1.3) */}
            {decomp && decomp.components.length > 0 && (
              <Card className="mt-4">
                <CardHeader
                  title="Risk allocation"
                  subtitle="where the 99% tail loss comes from — component ES by factor class (Euler; sums to book ES)"
                  right={<Link to="/risk/drill" className="text-xs text-fg-secondary hover:text-fg">drill ▸</Link>}
                />
                <CardBody>
                  <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-fg-secondary">
                    <span>diversified VaR <span className="tnum font-medium text-danger-fg">{lossText(decomp.diversified_var)}</span></span>
                    <span>Σ standalone VaR <span className="tnum text-fg-secondary">{lossText(decomp.sum_standalone_var)}</span></span>
                    <span>diversification benefit <span className="tnum font-medium text-success-fg">{lossText(decomp.diversification_benefit)}</span></span>
                  </div>
                  <div className="space-y-1.5">
                    {decomp.components.map((c) => {
                      const share = Math.max(0, c.es_share); // negative (hedging) shares render as empty bar
                      return (
                        <div key={c.class} className="flex items-center gap-2">
                          <div className="w-24 shrink-0 text-xs text-fg-secondary">{classLabel(c.class)}</div>
                          <div className="h-3 flex-1 overflow-hidden rounded bg-surface-0">
                            <div className={cn("h-full", c.component_es >= 0 ? "bg-danger" : "bg-success")} style={{ width: `${Math.min(100, Math.abs(c.es_share) * 100)}%` }} />
                          </div>
                          <div className="w-16 shrink-0 text-right tnum text-xs text-fg-secondary">{(share * 100).toFixed(0)}%</div>
                          <div className="w-20 shrink-0 text-right tnum text-xs text-fg-secondary" title={exactUsd(c.component_es)}>{lossText(c.component_es)}</div>
                        </div>
                      );
                    })}
                  </div>
                </CardBody>
              </Card>
            )}

            {/* Default risk — jump-to-default / incremental default charge (Tier 3.9) */}
            {jtd && jtd.issuers.length > 0 && (
              <Card className="mt-4">
                <CardHeader
                  title="Default risk — jump-to-default"
                  subtitle="loss if an issuer defaults now (LGD × exposure); IDR sums per-issuer net losses, no cross-name netting"
                />
                <CardBody>
                  <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-fg-secondary">
                    <span>IDR (default charge) <span className="tnum font-medium text-danger-fg">{lossText(jtd.idr)}</span></span>
                    {jtd.worst_issuer && (
                      <span>worst single name <span className="font-medium text-fg">{jtd.worst_issuer}</span> <span className="tnum text-danger-fg">{lossText(jtd.worst_jtd)}</span></span>
                    )}
                  </div>
                  <DataTable
                    columns={JTD_COLUMNS}
                    rows={[
                      ...jtd.issuers.map((i) => ({ ...i, isIndex: false })),
                      ...jtd.index_positions.map((i) => ({ ...i, isIndex: true })),
                    ]}
                    getRowKey={(i) => i.issuer}
                    rowClassName={(i) => (i.isIndex ? "text-fg-muted" : undefined)}
                    pageSize={25}
                    exportName="jtd_default_risk"
                    dense
                  />
                  <div className="mt-2 text-micro text-fg-muted">
                    Long bonds lose on default; bought protection gains. Index positions are diversified (single-name
                    equivalent shown) and excluded from the single-name IDR.
                  </div>
                </CardBody>
              </Card>
            )}

            {/* Scenarios */}
            {worstScenarios.length > 0 && (
              <Card className="mt-4">
                <CardHeader title="Stress scenarios" subtitle="full-revaluation P&L, worst first" />
                <CardBody>
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                    {worstScenarios.map((s) => (
                      <div key={s.scenario} className="rounded-md border border-subtle p-2">
                        <div className="truncate text-xs font-medium text-fg-secondary" title={s.scenario}>
                          {s.scenario}
                        </div>
                        <div className={cn("tnum text-sm font-semibold", s.total_pnl >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(s.total_pnl)}>
                          {humanizeUsd(s.total_pnl)}
                        </div>
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>
            )}
          </div>
        );
      }}
    </QueryState>
  );
}
