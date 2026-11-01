import { Link } from "react-router-dom";
import type { TradeResult } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { MetricGrid } from "@/components/shared/MetricGrid";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { buildMeasures, PLAIN_ENGLISH } from "./measures";
import { cn } from "@/lib/cn";

// PR-B2 Valuation Card — instrument-adaptive (PRICING_UI §5). Constant chrome
// (Value · Key measures · Legs · Diagnostics · Warnings · Provenance), contents shaped by
// the actual pricer output.

function LegBreakdown({ legs }: { legs: Record<string, number> }) {
  const entries = Object.entries(legs);
  if (entries.length === 0) return null;
  const max = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
  return (
    <div className="space-y-1.5">
      {entries.map(([name, v]) => (
        <div key={name} className="flex items-center gap-3">
          <span className="w-40 shrink-0 truncate font-mono text-xs text-fg-secondary" title={name}>
            {name}
          </span>
          <div className="relative h-4 flex-1 rounded bg-surface-0">
            <div
              className={cn("absolute top-0 h-4 rounded", v >= 0 ? "bg-success" : "bg-danger")}
              style={{ width: `${(Math.abs(v) / max) * 100}%` }}
            />
          </div>
          <span
            className={cn("tnum w-28 shrink-0 text-right text-xs", v >= 0 ? "text-success-fg" : "text-danger-fg")}
            title={exactUsd(v)}
          >
            {humanizeUsd(v)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function ValuationCard({ trade }: { trade: TradeResult }) {
  const pvTone = trade.pv >= 0 ? "text-success-fg" : "text-danger-fg";
  const measures = buildMeasures(trade.trade_type, trade.measures);
  const plain = PLAIN_ENGLISH[trade.trade_type];

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="font-mono">{trade.trade_id}</span>
            <span className="font-normal text-fg-secondary">{trade.description}</span>
          </span>
        }
        subtitle={`${trade.trade_type} · ${trade.currency} · val ${trade.valuation_date}`}
        right={
          <div className="flex items-center gap-2">
            <Link
              to={`/pricing/trade/${trade.trade_id}/lineage`}
              className="rounded bg-primary px-2.5 py-1 text-xs font-medium text-primary-fg hover:bg-primary-hover"
            >
              Explain ▸
            </Link>
            <Link
              to={`/pricing/trade/${trade.trade_id}/history`}
              className="rounded border border-strong px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-2"
            >
              History
            </Link>
          </div>
        }
      />
      <CardBody className="space-y-5">
        <div>
          <div className="text-xs uppercase tracking-wide text-fg-muted">Present value</div>
          <div className={cn("tnum text-3xl font-semibold", pvTone)} title={exactUsd(trade.pv)}>
            {humanizeUsd(trade.pv)}
          </div>
        </div>

        {trade.warnings.length > 0 && (
          <WarningBanner tone="warn" title={`${trade.warnings.length} warning(s)`}>
            {trade.warnings.join(" · ")}
          </WarningBanner>
        )}

        {measures.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">
              Key measures
            </div>
            <MetricGrid metrics={measures} columns={4} />
          </div>
        )}

        {Object.keys(trade.leg_pvs).length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">Legs</div>
            <LegBreakdown legs={trade.leg_pvs} />
          </div>
        )}

        {Object.keys(trade.diagnostics).length > 0 && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-fg-muted">
              Diagnostics
            </div>
            <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-fg-secondary">
              {Object.entries(trade.diagnostics).map(([k, v]) => (
                <span key={k}>
                  <span className="text-fg-muted">{k.replace(/_/g, " ")}:</span> {v}
                </span>
              ))}
            </div>
          </div>
        )}

        {plain && (
          <div className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-secondary">{plain}</div>
        )}

        <div className="flex items-center justify-between border-t border-subtle pt-3">
          <ProvenanceBadge
            items={{
              run_id: trade.provenance.run_id,
              snapshot_id: trade.provenance.market_data_snapshot_id,
              portfolio_version: trade.provenance.portfolio_version,
              git_sha: trade.provenance.git_sha,
              config_hash: trade.provenance.config_hash,
            }}
          />
          <span className="text-micro text-fg-muted">{trade.provenance.pricer_class}</span>
        </div>
      </CardBody>
    </Card>
  );
}
