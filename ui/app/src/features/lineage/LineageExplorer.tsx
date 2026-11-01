import { useState } from "react";
import { Link } from "react-router-dom";
import { useBook, useLineage, useProvenance } from "@/lib/api";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { humanizeUsd, exactUsd, fmtDuration } from "@/lib/format";
import { cn } from "@/lib/cn";

// Lineage Explorer — the unified, cross-subsystem drill-down. L1 stitches a trade's
// valuation + risk + market-data lineage into one view; L3 pivots by a provenance id.

function TradeLineage() {
  const book = useBook();
  const trades = book.data?.trades ?? [];
  const [tradeId, setTradeId] = useState("TRADE_22");
  const q = useLineage(tradeId);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-fg-secondary">Trade</span>
        <select className="rounded border border-strong px-2 py-1 text-xs" value={tradeId} onChange={(e) => setTradeId(e.target.value)}>
          {trades.map((t) => (
            <option key={t.trade_id} value={t.trade_id}>{t.trade_id} — {t.description}</option>
          ))}
        </select>
      </div>

      <QueryState query={q}>
        {(d) => {
          if (!d.available) {
            return (
              <WarningBanner tone="info" title="Lineage not exposed for this instrument">
                {d.reason} — <Link to={`/pricing/trade/${tradeId}`} className="underline">open the trade valuation</Link>.
              </WarningBanner>
            );
          }
          return (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="text-sm text-fg-secondary">{d.description}</div>
                <div className="flex gap-2">
                  <Link to={`/pricing/trade/${tradeId}/lineage`} className="rounded bg-primary px-2.5 py-1 text-xs font-medium text-primary-fg hover:bg-primary-hover">Full calculation lineage ▸</Link>
                  <Link to="/risk/methodology" className="rounded border border-strong px-2.5 py-1 text-xs text-fg-secondary hover:bg-surface-2">Risk methodology ▸</Link>
                </div>
              </div>

              {/* valuation lane */}
              <Card>
                <CardHeader title="Valuation" subtitle="pricing" />
                <CardBody>
                  <div className="flex items-end gap-6">
                    <div>
                      <div className="text-micro uppercase text-fg-muted">PV</div>
                      <div className={cn("tnum text-2xl font-semibold", (d.final?.pv ?? 0) >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(d.final?.pv ?? 0)}>{humanizeUsd(d.final?.pv)}</div>
                    </div>
                    {Object.entries(d.final?.leg_pvs ?? {}).map(([leg, v]) => (
                      <div key={leg}>
                        <div className="text-micro uppercase text-fg-muted">{leg}</div>
                        <div className="tnum text-fg">{humanizeUsd(v)}</div>
                      </div>
                    ))}
                  </div>
                  {d.plain_english && <div className="mt-2 text-xs text-fg-secondary">{d.plain_english}</div>}
                </CardBody>
              </Card>

              {/* risk lane */}
              {d.sensitivity_lineage && d.sensitivity_lineage.length > 0 && (
                <Card>
                  <CardHeader title="Risk" subtitle="bump-and-revalue (shared with the pricing engine)" />
                  <CardBody className="space-y-2">
                    {d.sensitivity_lineage.map((s) => (
                      <div key={s.factor} className="flex flex-wrap items-center justify-between gap-2 rounded border border-subtle px-3 py-2 text-sm">
                        <span className="font-mono text-fg">{s.factor}</span>
                        <span className="text-xs text-fg-muted">base {humanizeUsd(s.base_pv)} · +{humanizeUsd(s.up_pv)} · −{humanizeUsd(s.down_pv)}</span>
                        <span className={cn("tnum font-medium", s.value >= 0 ? "text-success-fg" : "text-danger-fg")}>{humanizeUsd(s.value)} <span className="text-xs text-fg-muted">{s.bump_unit}</span></span>
                      </div>
                    ))}
                  </CardBody>
                </Card>
              )}

              {/* market-data lane */}
              <Card>
                <CardHeader title="Market data" subtitle="inputs" right={<Link to="/marketdata" className="text-xs text-fg-secondary hover:text-fg">feeds ▸</Link>} />
                <CardBody className="space-y-1.5">
                  {(d.inputs?.market_data ?? []).map((m, i) => (
                    <div key={i} className="text-xs text-fg-secondary">
                      <span className="font-medium">{m.kind}</span>
                      {m.index && <span className="text-fg-secondary"> · {m.index}</span>}
                      {m.ticker && <span className="text-fg-secondary"> · {m.ticker}</span>}
                      {m.purpose && <span className="ml-1 rounded bg-surface-0 px-1 text-nano text-fg-secondary">{m.purpose}</span>}
                      {(m.as_of_date || d.inputs?.curve_as_of) && <span className="ml-1 text-fg-muted">as-of {m.as_of_date ?? d.inputs?.curve_as_of}</span>}
                    </div>
                  ))}
                </CardBody>
              </Card>

              <div className="flex justify-end">
                <ProvenanceBadge items={{ run_id: d.provenance?.run_id, snapshot_id: d.provenance?.market_data_snapshot_id, git_sha: d.provenance?.git_sha }} />
              </div>
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}

function statusZone(s: string): ZoneStatus {
  return s === "ok" ? "green" : s === "partial" ? "amber" : s === "failed" ? "red" : "unknown";
}

function ProvenancePivot() {
  const [snapshot, setSnapshot] = useState("680bb0b17b1c72c0");
  const q = useProvenance({ snapshot_id: snapshot });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-fg-secondary">Snapshot id</span>
        <input value={snapshot} onChange={(e) => setSnapshot(e.target.value.trim())} className="w-72 rounded border border-strong px-2 py-1 font-mono text-xs" />
      </div>
      <p className="text-xs text-fg-secondary">Every run that consumed this market-data snapshot — the data-side answer to "what shares this input".</p>
      <QueryState query={q} empty={(d) => d.count === 0}>
        {(d) => (
          <Card>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Module</th>
                    <th className="px-4 py-2 font-medium">Run</th>
                    <th className="px-4 py-2 font-medium">Date</th>
                    <th className="px-4 py-2 text-right font-medium">Duration</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {d.runs.map((r) => (
                    <tr key={`${r.module}-${r.run_id}`} className="hover:bg-surface-2">
                      <td className="px-4 py-2"><span className="rounded bg-surface-0 px-1.5 py-0.5 text-xs text-fg-secondary">{r.module}</span></td>
                      <td className="px-4 py-2">
                        <Link to={`/ops/trace/${r.run_id}`} className="font-mono text-xs text-fg hover:underline">{r.run_id.slice(0, 12)}</Link>
                      </td>
                      <td className="px-4 py-2 tnum text-fg-secondary">{r.date}</td>
                      <td className="px-4 py-2 text-right tnum text-fg-secondary">{fmtDuration(r.duration_s)}</td>
                      <td className="px-4 py-2"><Zone status={statusZone(r.status)} label={r.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </QueryState>
    </div>
  );
}

export function LineageExplorer() {
  const [tab, setTab] = useState<"trade" | "provenance">("trade");
  return (
    <div>
      <PageHeader title="Lineage Explorer" subtitle="From any number to its sources — valuation + risk + market data, unified, pinned to provenance." />
      <div className="mb-5 flex gap-1 border-b border">
        {([["trade", "Trade Lineage"], ["provenance", "Provenance"]] as const).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)} className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium", tab === k ? "border-primary text-primary" : "border-transparent text-fg-secondary hover:text-fg")}>
            {label}
          </button>
        ))}
      </div>
      {tab === "trade" ? <TradeLineage /> : <ProvenancePivot />}
    </div>
  );
}
