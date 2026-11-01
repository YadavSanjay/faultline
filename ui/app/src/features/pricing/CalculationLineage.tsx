import { Link, useParams } from "react-router-dom";
import { useLineage, type LineageResponse } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { CurveChart } from "@/components/shared/CurveChart";
import { HazardCurveChart } from "@/components/shared/HazardCurveChart";
import { SpreadCurveChart } from "@/components/shared/SpreadCurveChart";
import { VolSurfaceHeatmap } from "@/components/shared/VolSurfaceHeatmap";
import { CashflowLadder } from "./CashflowLadder";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// PR-B3 Calculation Lineage — the centerpiece. Renders the real computation top→down as a
// three-tier ladder (final → intermediates → inputs), every leaf traceable, with the
// arithmetic shown and reconciliations ticked.

function fmtCompValue(v: number | string | null): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return Math.abs(v) >= 1000 ? humanizeUsd(v) : v.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function fmtCell(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    return Math.abs(v) >= 1000 ? humanizeUsd(v) : v.toLocaleString("en-US", { maximumFractionDigits: 5 });
  }
  return String(v);
}

function Tier({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-fg">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-micro text-primary-fg">
          {n}
        </span>
        {title}
      </h2>
      {children}
    </section>
  );
}

function ReconChips({ recon }: { recon: { expr: string; value: number; ok: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {recon.map((r) => (
        <span
          key={r.expr}
          className={cn(
            "inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs",
            r.ok ? "bg-success-tint text-success-fg" : "bg-danger-tint text-danger-fg",
          )}
          title={exactUsd(r.value)}
        >
          <span className="text-fg-secondary">{r.expr} =</span>
          <span className="tnum font-medium">{humanizeUsd(r.value)}</span>
          <span>{r.ok ? "✓" : "✗"}</span>
        </span>
      ))}
    </div>
  );
}

function StaticTicket({ stat }: { stat: Record<string, unknown> }) {
  const conventions = (stat.conventions ?? {}) as Record<string, unknown>;
  const scalars = Object.entries(stat).filter(([k]) => k !== "conventions");
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        {scalars.map(([k, v]) => (
          <span key={k}>
            <span className="text-fg-muted">{k.replace(/_/g, " ")}:</span>{" "}
            <span className="text-fg">{String(v)}</span>
          </span>
        ))}
      </div>
      {Object.keys(conventions).length > 0 && (
        <div className="flex flex-wrap gap-x-6 gap-y-1 border-t border-subtle pt-2">
          {Object.entries(conventions).map(([k, v]) => (
            <span key={k}>
              <span className="text-fg-muted">{k}:</span> <span className="text-fg">{String(v)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function LineageBody({ data }: { data: LineageResponse }) {
  if (!data.available) {
    return (
      <WarningBanner tone="info" title="Lineage not available">
        {data.reason}
      </WarningBanner>
    );
  }
  const fin = data.final!;
  const inter = data.intermediates!;
  const inputs = data.inputs!;
  const curves = Object.entries(inter.curves ?? {});

  return (
    <div className="space-y-6">
      {data.plain_english && (
        <div className="rounded-md border-l-2 border-primary bg-primary-tint px-4 py-2.5 text-sm text-fg-secondary">{data.plain_english}</div>
      )}

      {/* ① FINAL */}
      <Tier n="①" title="Final value">
        <Card>
          <CardBody className="space-y-3">
            <div className="flex items-end gap-6">
              <div>
                <div className="text-xs uppercase tracking-wide text-fg-muted">Present value</div>
                <div
                  className={cn("tnum text-3xl font-semibold", fin.pv >= 0 ? "text-success-fg" : "text-danger-fg")}
                  title={exactUsd(fin.pv)}
                >
                  {humanizeUsd(fin.pv)}
                </div>
              </div>
              {Object.entries(fin.leg_pvs).map(([leg, v]) => (
                <div key={leg}>
                  <div className="text-xs uppercase tracking-wide text-fg-muted">{leg}</div>
                  <div className="tnum text-lg font-medium text-fg" title={exactUsd(v)}>
                    {humanizeUsd(v)}
                  </div>
                </div>
              ))}
            </div>
            <ReconChips recon={fin.reconciliation} />
          </CardBody>
        </Card>
      </Tier>

      {/* ② INTERMEDIATES */}
      <Tier n="②" title="Intermediates — how the value is built">
        <div className="space-y-4">
          {curves.map(([name, curve]) => (
            <Card key={name}>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
                Curve {name} · as-of {inputs.curve_as_of ?? "—"}
                <span className="ml-1 text-fg-muted">(par → zero → discount factor → forward)</span>
              </div>
              <CardBody>
                <CurveChart curve={curve} />
              </CardBody>
            </Card>
          ))}

          {inter.hazard_curve && inter.hazard_curve.length > 0 && (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
                Issuer hazard curve — bootstrapped from the CDS par-spread term structure
                <span className="ml-1 text-fg-muted">(par spread → piecewise-flat forward hazard → survival)</span>
              </div>
              <CardBody>
                <HazardCurveChart points={inter.hazard_curve} />
              </CardBody>
            </Card>
          )}

          {inter.vol_surface && (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
                Implied-vol surface — strike × expiry (IV%), {inter.vol_surface.underlier} @ spot {inter.vol_surface.spot}
                <span className="ml-1 text-fg-muted">(down a column = put skew; across a row = term structure; ◇ marks each leg)</span>
              </div>
              <CardBody>
                <VolSurfaceHeatmap surface={inter.vol_surface} />
              </CardBody>
            </Card>
          )}

          {inter.credit_spread_curve && inter.credit_spread_curve.length > 0 && (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
                Issuer credit curve — Z-spread over SOFR by tenor
                <span className="ml-1 text-fg-muted">(per-tenor CDS spread + SOFR-Treasury basis; each cashflow discounts on this curve)</span>
              </div>
              <CardBody>
                <SpreadCurveChart points={inter.credit_spread_curve} />
              </CardBody>
            </Card>
          )}

          {inter.cashflows && inter.cashflows.length > 0 && (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
                Cashflow ladder — each flow discounted; the column sum is the leg PV
              </div>
              <CardBody>
                <CashflowLadder cashflows={inter.cashflows} legPvs={fin.leg_pvs} />
              </CardBody>
            </Card>
          )}

          {inter.computation && inter.computation.length > 0 && (
            <Card>
              <CardBody>
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  {inter.computation.map((s, i) => (
                    <span key={s.step} className="flex items-center gap-2">
                      {i > 0 && <span className="text-fg-muted">→</span>}
                      <span className="rounded bg-surface-2 px-2 py-1">
                        <span className="text-fg-muted">{s.step}:</span>{" "}
                        <span className="tnum font-medium text-fg">{fmtCompValue(s.value)}</span>
                      </span>
                    </span>
                  ))}
                </div>
              </CardBody>
            </Card>
          )}

          {(inter.tables ?? []).map((tbl) => (
            <Card key={tbl.title}>
              <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">{tbl.title}</div>
              <div className="overflow-auto">
                <table className="w-full text-xs">
                  <thead className="bg-surface-2 text-left text-fg-secondary">
                    <tr>
                      {tbl.columns.map((col) => (
                        <th key={col} className="px-3 py-1.5 font-medium">{col.replace(/_/g, " ")}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {tbl.rows.map((row, ri) => (
                      <tr key={ri} className="hover:bg-surface-2">
                        {tbl.columns.map((col) => (
                          <td key={col} className="px-3 py-1.5 tnum text-fg">{fmtCell(row[col])}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      </Tier>

      {/* ③ INPUTS */}
      <Tier n="③" title="Inputs — market data & static">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">Market data</div>
            <CardBody className="space-y-2">
              {(inputs.market_data ?? []).map((m, i) => (
                <div key={i} className="flex items-center justify-between rounded border border-subtle px-3 py-2 text-xs">
                  <div>
                    <span className="font-medium text-fg">{m.kind}</span>
                    {m.index && <span className="text-fg-secondary"> · {m.index}</span>}
                    {m.ticker && <span className="text-fg-secondary"> · {m.ticker}</span>}
                    {m.purpose && <span className="ml-1 rounded bg-surface-0 px-1 text-nano text-fg-secondary">{m.purpose}</span>}
                    <div className="mt-0.5 text-fg-muted">
                      {m.as_of_date && `as-of ${m.as_of_date} · `}
                      {(m.source ?? inputs.curve_source) && `source ${m.source ?? inputs.curve_source}`}
                    </div>
                  </div>
                  <Link to="/marketdata" className="text-fg-muted hover:text-fg-secondary">
                    feed ▸
                  </Link>
                </div>
              ))}
            </CardBody>
          </Card>

          <Card>
            <div className="border-b border-subtle px-4 py-2 text-xs font-medium text-fg-secondary">
              Static / reference (trade ticket)
            </div>
            <CardBody>
              <StaticTicket stat={inputs.static ?? {}} />
            </CardBody>
          </Card>
        </div>
      </Tier>

      <div className="flex items-center justify-between border-t border-subtle pt-3">
        <ProvenanceBadge
          items={{
            run_id: data.provenance?.run_id,
            snapshot_id: data.provenance?.market_data_snapshot_id,
            git_sha: data.provenance?.git_sha,
            config_hash: data.provenance?.config_hash,
          }}
        />
        <span className="text-micro text-fg-muted">explain pass · {data.provenance?.quantlib_version}</span>
      </div>
    </div>
  );
}

export function CalculationLineage() {
  const { tradeId = "" } = useParams();
  const query = useLineage(tradeId);

  return (
    <div className="space-y-4">
      <nav className="text-xs text-fg-secondary">
        <Link to="/pricing/book" className="hover:underline">
          Book
        </Link>
        <span className="mx-1 text-fg-muted">›</span>
        <Link to={`/pricing/trade/${tradeId}`} className="font-mono hover:underline">
          {tradeId}
        </Link>
        <span className="mx-1 text-fg-muted">›</span>
        <span>Lineage</span>
      </nav>
      <QueryState query={query}>{(data) => <LineageBody data={data} />}</QueryState>
    </div>
  );
}
