import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useRiskReport, type RiskReport, type TradeSensitivities } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { classLabel, factorCaption, lossText } from "./format";
import { cn } from "@/lib/cn";

type By = "risk" | "asset" | "factor" | "trade";

function sensValue(t: TradeSensitivities, factor: string): number {
  return t.sensitivities.find((s) => s.factor === factor)?.value ?? 0;
}

// Tier 1.3 — VaR decomposition: standalone vs diversified, Euler component ES by factor class.
function ByRiskAllocation({ report }: { report: RiskReport }) {
  const decomp = report.var?.decomposition;
  const byLevel = decomp?.by_level ?? {};
  const levelKeys = Object.keys(byLevel).sort((a, b) => Number(a) - Number(b));
  const [level, setLevel] = useState(levelKeys.includes("0.99") ? "0.99" : levelKeys[levelKeys.length - 1] ?? "");
  const L = byLevel[level];

  if (!decomp || !L) {
    return <Card><CardBody><div className="text-sm text-fg-muted">No VaR decomposition in this run.</div></CardBody></Card>;
  }

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs text-fg-secondary">Confidence</span>
        <select className="rounded border border-strong px-2 py-1 text-xs" value={level} onChange={(e) => setLevel(e.target.value)}>
          {levelKeys.map((k) => (
            <option key={k} value={k}>{(Number(k) * 100).toFixed(1)}%</option>
          ))}
        </select>
      </div>

      <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Card><CardBody>
          <div className="text-micro uppercase text-fg-muted">Diversified VaR</div>
          <div className="tnum text-lg font-semibold text-danger-fg" title={exactUsd(L.diversified_var)}>{lossText(L.diversified_var)}</div>
          <div className="text-micro text-fg-muted">the book's actual VaR (factors co-move)</div>
        </CardBody></Card>
        <Card><CardBody>
          <div className="text-micro uppercase text-fg-muted">Σ standalone VaR</div>
          <div className="tnum text-lg font-semibold text-fg-secondary" title={exactUsd(L.sum_standalone_var)}>{lossText(L.sum_standalone_var)}</div>
          <div className="text-micro text-fg-muted">each class alone, summed (no netting)</div>
        </CardBody></Card>
        <Card><CardBody>
          <div className="text-micro uppercase text-fg-muted">Diversification benefit</div>
          <div className="tnum text-lg font-semibold text-success-fg" title={exactUsd(L.diversification_benefit)}>{lossText(L.diversification_benefit)}</div>
          <div className="text-micro text-fg-muted">Σ standalone − diversified</div>
        </CardBody></Card>
      </div>

      <Card>
        <CardHeader title="By factor class" subtitle="component ES is the Euler tail-allocation — it sums to the book ES" />
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
            <tr>
              <th className="px-4 py-2 font-medium">Factor class</th>
              <th className="px-4 py-2 text-right font-medium">Standalone VaR</th>
              <th className="px-4 py-2 text-right font-medium">Standalone ES</th>
              <th className="px-4 py-2 text-right font-medium">Component ES</th>
              <th className="px-4 py-2 text-right font-medium">Share of tail</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {L.components.map((c) => (
              <tr key={c.class} className="hover:bg-surface-2">
                <td className="px-4 py-2 text-fg">{classLabel(c.class)}</td>
                <td className="px-4 py-2 text-right tnum text-fg-secondary" title={exactUsd(c.standalone_var)}>{lossText(c.standalone_var)}</td>
                <td className="px-4 py-2 text-right tnum text-fg-secondary" title={exactUsd(c.standalone_es)}>{lossText(c.standalone_es)}</td>
                <td className={cn("px-4 py-2 text-right tnum font-medium", c.component_es >= 0 ? "text-danger-fg" : "text-success-fg")} title={exactUsd(c.component_es)}>
                  {c.component_es >= 0 ? lossText(c.component_es) : `+${lossText(c.component_es)}`}
                </td>
                <td className="px-4 py-2 text-right tnum text-fg-secondary">{(c.es_share * 100).toFixed(1)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="mt-2 text-micro text-fg-muted">
        A negative component ES means the class <em>reduces</em> the book's tail loss (a hedge on the days the whole book is worst).
      </div>
    </div>
  );
}

function ByAssetClass({ report }: { report: RiskReport }) {
  const sens = report.sensitivities ?? [];
  const groups = useMemo(() => {
    const m = new Map<string, Record<string, number>>();
    for (const t of sens) {
      const g = m.get(t.asset_class) ?? {};
      for (const s of t.sensitivities) g[s.factor] = (g[s.factor] ?? 0) + s.value;
      m.set(t.asset_class, g);
    }
    return m;
  }, [sens]);

  return (
    <div className="space-y-3">
      {[...groups.entries()].map(([ac, factors]) => (
        <Card key={ac}>
          <div className="border-b border-subtle px-4 py-2 text-sm font-semibold capitalize text-fg">{ac}</div>
          <CardBody>
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              {Object.entries(factors)
                .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                .map(([f, v]) => (
                  <span key={f} className="text-xs">
                    <span className="font-mono text-fg-secondary">{f}</span>{" "}
                    <span className={cn("tnum", v >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(v)}>
                      {humanizeUsd(v)}
                    </span>
                  </span>
                ))}
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

function ByFactor({ report }: { report: RiskReport }) {
  const factors = Object.keys(report.book_sensitivities ?? {}).sort();
  const [factor, setFactor] = useState(factors[0] ?? "");
  const sens = report.sensitivities ?? [];
  const rows = sens
    .map((t) => ({ trade: t, v: sensValue(t, factor) }))
    .filter((r) => r.v !== 0)
    .sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const stdev = report.var?.factor_stdev?.[factor];

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs text-fg-secondary">Factor</span>
        <select className="rounded border border-strong px-2 py-1 text-xs" value={factor} onChange={(e) => setFactor(e.target.value)}>
          {factors.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
        <span className="text-xs text-fg-muted">{factorCaption(factor)}</span>
        {stdev != null && <span className="text-xs text-fg-muted">· daily σ {stdev.toExponential(2)}</span>}
      </div>
      <Card>
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
            <tr>
              <th className="px-4 py-2 font-medium">Trade</th>
              <th className="px-4 py-2 font-medium">Asset class</th>
              <th className="px-4 py-2 text-right font-medium">Exposure</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {rows.map(({ trade, v }) => (
              <tr key={trade.trade_id} className="hover:bg-surface-2">
                <td className="px-4 py-2">
                  <Link to={`/pricing/trade/${trade.trade_id}`} className="font-mono text-fg hover:underline">
                    {trade.trade_id}
                  </Link>
                </td>
                <td className="px-4 py-2 capitalize text-fg-secondary">{trade.asset_class}</td>
                <td className={cn("px-4 py-2 text-right tnum", v >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(v)}>
                  {humanizeUsd(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function ByTrade({ report }: { report: RiskReport }) {
  const sens = (report.sensitivities ?? []).filter((t) => t.sensitivities.length > 0);
  const [tradeId, setTradeId] = useState(sens[0]?.trade_id ?? "");
  const trade = sens.find((t) => t.trade_id === tradeId);

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-xs text-fg-secondary">Trade</span>
        <select className="rounded border border-strong px-2 py-1 text-xs" value={tradeId} onChange={(e) => setTradeId(e.target.value)}>
          {sens.map((t) => (
            <option key={t.trade_id} value={t.trade_id}>
              {t.trade_id} — {t.description}
            </option>
          ))}
        </select>
      </div>
      {trade && (
        <Card>
          <CardBody>
            <div className="mb-2 text-xs text-fg-secondary">
              PV <span className="tnum text-fg">{humanizeUsd(trade.pv)}</span> · {trade.sensitivities.length} factors
            </div>
            <div className="divide-y divide-border-subtle">
              {trade.sensitivities.map((s) => (
                <div key={s.factor} className="flex items-baseline justify-between py-1">
                  <div>
                    <span className="font-mono text-xs text-fg">{s.factor}</span>{" "}
                    <span className="text-micro text-fg-muted">{s.bump_unit}</span>
                  </div>
                  <span className={cn("tnum text-sm", s.value >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(s.value)}>
                    {humanizeUsd(s.value)}
                  </span>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

export function RiskDrill() {
  const query = useRiskReport();
  const [by, setBy] = useState<By>("risk");

  const label = (b: By) => (b === "asset" ? "asset class" : b === "risk" ? "risk (VaR)" : b);

  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(report) => (
        <div>
          <div className="mb-4 inline-flex rounded-md border border bg-surface-1 p-0.5 text-xs">
            {(["risk", "asset", "factor", "trade"] as By[]).map((b) => (
              <button
                key={b}
                type="button"
                onClick={() => setBy(b)}
                className={cn("rounded px-2.5 py-1 font-medium capitalize", by === b ? "bg-primary text-primary-fg" : "text-fg-secondary")}
              >
                by {label(b)}
              </button>
            ))}
          </div>
          {by === "risk" && <ByRiskAllocation report={report} />}
          {by === "asset" && <ByAssetClass report={report} />}
          {by === "factor" && <ByFactor report={report} />}
          {by === "trade" && <ByTrade report={report} />}
        </div>
      )}
    </QueryState>
  );
}
