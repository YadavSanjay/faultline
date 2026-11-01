import { useState } from "react";
import type { EChartsOption } from "echarts";
import { useRiskReport, type Backtest } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { EChart } from "@/components/shared/EChart";
import { MetricGrid } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { cn } from "@/lib/cn";

// RK-B5 Backtest, rebuilt: the two backtests the engine runs — sensitivity VaR and the
// full-revaluation VaR — side by side, each with the Basel traffic-light and the Kupiec /
// Christoffersen coverage tests, an observed-vs-expected exceptions chart, and the dated
// per-day exception timeline (so clustering is visible, not just the count).

const VERDICT: Record<string, string> = {
  green: "Forecasting risk adequately — exceptions are within the expected band.",
  amber: "Borderline — more exceptions than expected; review the model.",
  red: "Under-forecasting risk — the model must be reviewed.",
};

// χ² critical values (95%): POF & independence ~ χ²₁ = 3.84; conditional coverage ~ χ²₂ = 5.99.
function lrTone(lr: number | undefined, crit: number): "gain" | "loss" | "neutral" {
  if (lr == null) return "neutral";
  return lr < crit ? "gain" : "loss";
}

function pass(lr: number | undefined, crit: number): string {
  if (lr == null) return "—";
  return lr < crit ? "pass" : "reject";
}

function BacktestCard({ title, bt }: { title: string; bt: Backtest }) {
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        <Zone status={bt.zone as ZoneStatus} label={`${bt.zone} zone`} />
      </div>
      <CardBody className="space-y-3">
        <div className="text-xs text-fg-secondary">{VERDICT[bt.zone] ?? ""}</div>
        <MetricGrid
          columns={3}
          metrics={[
            {
              label: "Exceptions",
              value: bt.n_exceptions,
              unit: `/ ${bt.n_days}d`,
              caption: `expected ${bt.expected_exceptions.toFixed(1)}`,
              tone: bt.n_exceptions > bt.expected_exceptions ? "loss" : "gain",
            },
            { label: "Exception rate", value: `${(bt.exception_rate * 100).toFixed(2)}%`, caption: `vs ${((1 - bt.level) * 100).toFixed(0)}% target` },
            { label: "Confidence", value: `${(bt.level * 100).toFixed(1)}%`, caption: `window ${bt.window}d` },
          ]}
        />
        <div className="grid grid-cols-3 gap-2 border-t border-subtle pt-3 text-xs">
          {[
            { k: "Kupiec POF", lr: bt.kupiec_lr, crit: 3.84, hint: "unconditional coverage (χ²₁)" },
            { k: "Christoffersen", lr: bt.christoffersen_lr, crit: 3.84, hint: "exception independence (χ²₁)" },
            { k: "Cond. coverage", lr: bt.cc_lr, crit: 5.99, hint: "joint CC (χ²₂)" },
          ].map((t) => (
            <div key={t.k}>
              <div className="text-micro uppercase tracking-wide text-fg-muted">{t.k}</div>
              <div className={cn("tnum text-sm font-semibold", { gain: "text-success-fg", loss: "text-danger-fg", neutral: "text-fg" }[lrTone(t.lr, t.crit)])}>
                {t.lr == null ? "—" : t.lr.toFixed(2)}
                <span className="ml-1 text-nano font-normal text-fg-muted">{pass(t.lr, t.crit)}</span>
              </div>
              <div className="text-nano text-fg-muted">{t.hint}</div>
            </div>
          ))}
        </div>
        <div className="text-micro text-fg-muted">{bt.method.replace(/_/g, " ")}</div>
      </CardBody>
    </Card>
  );
}

function ExceptionTimeline({ methods }: { methods: { name: string; bt: Backtest }[] }) {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const withTl = methods.filter((m) => (m.bt.timeline?.length ?? 0) > 0);
  const [sel, setSel] = useState(0);
  if (withTl.length === 0) return null;
  const active = withTl[Math.min(sel, withTl.length - 1)];
  const tl = active.bt.timeline ?? [];
  const dates = tl.map((p) => p.date.slice(0, 10));

  const option: EChartsOption = {
    grid: { left: 60, right: 16, top: 28, bottom: 40 },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number }[];
        const i = arr[0]?.dataIndex ?? 0;
        const p = tl[i];
        if (!p) return "";
        return `${p.date.slice(0, 10)}<br/>P&L ${exactUsd(p.pnl)}<br/>VaR limit −${humanizeUsd(p.var)}${p.exception ? `<br/><b style='color:${c.neg}'>exception</b>` : ""}`;
      },
    },
    legend: { top: 0, data: ["Daily P&L", "VaR limit", "Exception"], textStyle: { fontSize: 11 } },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 9, hideOverlap: true } },
    yAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, formatter: (v: number) => humanizeUsd(v) } },
    series: [
      { name: "Daily P&L", type: "line", showSymbol: false, data: tl.map((p) => p.pnl), lineStyle: { width: 1, color: c.neutral }, areaStyle: { opacity: 0.04, color: c.neutral } },
      { name: "VaR limit", type: "line", showSymbol: false, data: tl.map((p) => -p.var), lineStyle: { type: "dashed", color: c.warning, width: 1 } },
      {
        name: "Exception",
        type: "scatter",
        symbolSize: 9,
        itemStyle: { color: c.neg },
        data: tl.map((p, i) => (p.exception ? [i, p.pnl] : null)).filter((d): d is [number, number] => d !== null),
      },
    ],
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-2">
        <h3 className="text-sm font-semibold text-fg">
          Exception timeline <span className="font-normal text-fg-muted">· realized P&amp;L vs the rolling VaR limit</span>
        </h3>
        {withTl.length > 1 && (
          <div className="flex gap-1 text-xs">
            {withTl.map((m, i) => (
              <button
                key={m.name}
                type="button"
                onClick={() => setSel(i)}
                className={cn("rounded px-2 py-0.5", i === sel ? "bg-primary text-primary-fg" : "text-fg-secondary hover:bg-surface-0")}
              >
                {m.name}
              </button>
            ))}
          </div>
        )}
      </div>
      <CardBody>
        <EChart option={option} height={260} />
        <div className="mt-2 text-micro text-fg-muted">
          A red dot marks a day whose realized loss pierced the VaR limit estimated from the prior {active.bt.window}-day
          window. {active.bt.n_exceptions} exception(s) over {active.bt.n_days} days — watch for clustering (the
          Christoffersen test above quantifies it).
        </div>
      </CardBody>
    </Card>
  );
}

export function BacktestPage() {
  const query = useRiskReport();
  const { theme } = useTheme();
  const c = chartColors(theme);
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(report) => {
        const bt = report.backtest;
        const btFr = report.backtest_full_reval;
        if (!bt && !btFr) {
          return (
            <WarningBanner tone="info" title="No backtest in this run">
              The VaR backtest needs enough aligned factor history (window + test days). It was not produced for this run.
            </WarningBanner>
          );
        }

        const methods = [
          bt && { name: "Sensitivity VaR", bt },
          btFr && { name: "Full-reval VaR", bt: btFr },
        ].filter(Boolean) as { name: string; bt: Backtest }[];

        const chart: EChartsOption = {
          grid: { left: 40, right: 16, top: 30, bottom: 24 },
          tooltip: { trigger: "axis" },
          legend: { top: 0, data: ["Observed", "Expected"], textStyle: { fontSize: 11 } },
          xAxis: { type: "category", data: methods.map((m) => m.name), axisLabel: { fontSize: 11 } },
          yAxis: { type: "value", name: "exceptions", nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10 } },
          series: [
            { name: "Observed", type: "bar", data: methods.map((m) => m.bt.n_exceptions), itemStyle: { color: c.neg }, barMaxWidth: 40 },
            { name: "Expected", type: "bar", data: methods.map((m) => Number(m.bt.expected_exceptions.toFixed(2))), itemStyle: { color: c.neutral }, barMaxWidth: 40 },
          ],
        };

        return (
          <div className="space-y-5">
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {methods.map((m) => (
                <BacktestCard key={m.name} title={m.name} bt={m.bt} />
              ))}
            </div>

            <ExceptionTimeline methods={methods} />

            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                Observed vs expected exceptions
              </div>
              <CardBody>
                <EChart option={chart} height={220} />
                <div className="mt-2 text-micro text-fg-muted">
                  An exception is a day whose realized loss exceeded the model's VaR estimated from the prior window.
                  Expected = (1 − confidence) × test days.
                </div>
              </CardBody>
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}
