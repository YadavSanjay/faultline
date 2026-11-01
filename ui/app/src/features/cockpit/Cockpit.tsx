import { Link } from "react-router-dom";
import { useBook, useRiskReport, useDq, useScenarioCatalog, usePosture } from "@/lib/api";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { lossText } from "@/features/risk/format";
import { cn } from "@/lib/cn";
import { SignalHealthBadge } from "@/features/scenarios/SignalHealth";

// The portfolio cockpit. A 5-second read across the four
// subsystems, each KPI a door into its workspace.

function Kpi({ to, label, value, sub, tone = "neutral" }: {
  to: string;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "neutral" | "gain" | "loss";
}) {
  const toneCls = tone === "gain" ? "text-success-fg" : tone === "loss" ? "text-danger-fg" : "text-fg";
  return (
    <Link to={to}>
      <Card className="h-full transition-colors hover:border-strong">
        <CardBody>
          <div className="text-xs uppercase tracking-wide text-fg-muted">{label}</div>
          <div className={cn("tnum mt-1 text-2xl font-semibold", toneCls)}>{value}</div>
          {sub && <div className="mt-0.5 text-xs text-fg-secondary">{sub}</div>}
        </CardBody>
      </Card>
    </Link>
  );
}

export function Cockpit() {
  const book = useBook();
  const risk = useRiskReport();
  const dq = useDq();
  const scenarios = useScenarioCatalog();
  const posture = usePosture();

  const b = book.data;
  const var99 = risk.data?.var?.var?.["0.99"];
  const es99 = risk.data?.var?.es?.["0.99"];
  const dqCounts = dq.data?.counts;
  const worst = [...(scenarios.data?.cells ?? [])].filter((c) => c.ran).sort((a, b2) => (a.total_pnl ?? 0) - (b2.total_pnl ?? 0))[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI-Themed Portfolio"
        subtitle="Portfolio cockpit — value, risk, data, and scenarios across the platform."
        right={<ProvenanceBadge items={{ run_id: b?.trades?.[0]?.run_id, snapshot_id: b?.trades?.[0]?.snapshot_id }} />}
      />

      {/* control posture */}
      {posture.data && (
        <Card>
          <CardBody className="flex flex-wrap items-center gap-3">
            <Zone status={posture.data.overall as ZoneStatus} label={`overall ${posture.data.overall}`} className="text-sm" />
            <span className="text-sm text-fg-secondary">Control posture</span>
            <SignalHealthBadge />
            <div className="ml-auto flex flex-wrap gap-1.5">
              {Object.entries(posture.data.tiles).map(([k, t]) => (
                <span key={k} title={`${k}: ${t.headline}`} className={cn("inline-block h-2.5 w-2.5 rounded-full", t.status === "green" ? "bg-success" : t.status === "amber" ? "bg-warning" : t.status === "red" ? "bg-danger" : "bg-border-strong")} />
              ))}
            </div>
            <Link to="/governance" className="text-xs text-fg-secondary hover:text-fg">control room ▸</Link>
          </CardBody>
        </Card>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Kpi
          to="/pricing"
          label="Book value"
          value={b ? humanizeUsd(b.total_pv) : "—"}
          sub={b ? `${b.trades.length} trades · val ${b.valuation_date}` : "loading…"}
          tone={(b?.total_pv ?? 0) >= 0 ? "gain" : "loss"}
        />
        <Kpi
          to="/risk"
          label="VaR 99% / ES"
          value={var99 != null ? lossText(var99) : "—"}
          sub={es99 != null ? `ES ${lossText(es99)} · backtest ${risk.data?.backtest?.zone ?? "—"}` : "loading…"}
          tone="loss"
        />
        <Kpi
          to="/marketdata/dq"
          label="Data quality"
          value={dqCounts ? `${dqCounts.ok ?? 0} ok` : "—"}
          sub={dqCounts ? `${dqCounts.warn ?? 0} warn · ${dqCounts.block ?? 0} block` : "loading…"}
          tone={dqCounts?.block ? "loss" : "neutral"}
        />
        <Kpi
          to={worst ? `/scenarios/${worst.scenario}?severity=${worst.severity}` : "/scenarios"}
          label="Worst scenario"
          value={worst ? humanizeUsd(worst.total_pnl) : "—"}
          sub={worst ? `${worst.scenario} · ${worst.severity}` : "loading…"}
          tone="loss"
        />
      </div>

      {/* book by asset class */}
      {b && b.by_asset_class.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-fg-secondary">Book by asset class</h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {b.by_asset_class.map((a) => (
              <Link key={a.asset_class} to="/pricing">
                <Card className="transition-colors hover:border-strong">
                  <CardBody>
                    <div className="text-xs font-medium capitalize text-fg-secondary">{a.asset_class}</div>
                    <div className="tnum text-base font-semibold text-fg" title={exactUsd(a.total_pv ?? 0)}>{humanizeUsd(a.total_pv)}</div>
                    <div className="text-micro text-fg-muted">{a.n_trades} trades</div>
                  </CardBody>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
