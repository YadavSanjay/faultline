import type { EChartsOption } from "echarts";
import { useSizing, type SizingProfile, type ExpectedReturnRow } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { EChart } from "@/components/shared/EChart";
import { useTheme } from "@/lib/theme";
import { chartColors } from "@/lib/chartTheme";
import { MetricGrid } from "@/components/shared/MetricGrid";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { PROFILE_ORDER, themeFor, signedPct, methodLabel } from "./format";

// Risk-return frontier: the three mandates plotted risk (VaR) vs expected return, plus the
// return-driver derivation (expected_returns method + detail) and the trade-covariance panel
// that the sizing optimiser is built on — all from the stored sizing report.

// mandate → chart color, mapped onto theme-aware semantic hues.
function profileColor(profile: string, c: ReturnType<typeof chartColors>): string {
  if (profile === "balanced") return c.info;
  if (profile === "aggressive") return c.pos;
  if (profile === "risky") return c.neg;
  return c.neutral;
}

export function Frontier() {
  const query = useSizing();
  const { theme } = useTheme();
  const c = chartColors(theme);
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(data) => {
        const profiles = PROFILE_ORDER.map((k) => data.profiles[k]).filter(Boolean) as SizingProfile[];
        const ordered = [...profiles].sort((a, b) => a.achieved_var99 - b.achieved_var99);
        const er = data.expected_returns ?? {};
        const erRows = (Object.values(er) as ExpectedReturnRow[])
          .filter((r) => r.method && r.method !== "none")
          .sort((a, b) => Math.abs(b.mu_annual) - Math.abs(a.mu_annual));

        const chart: EChartsOption = {
          grid: { left: 72, right: 24, top: 24, bottom: 40 },
          tooltip: {
            trigger: "item",
            formatter: (p: unknown) => {
              const d = p as { data: { name: string; value: [number, number] } };
              return `${d.data.name}<br/>VaR ${humanizeUsd(d.data.value[0])} · return ${humanizeUsd(d.data.value[1])}`;
            },
          },
          xAxis: {
            type: "value", name: "1-day 99% VaR (risk)", nameLocation: "middle", nameGap: 28,
            nameTextStyle: { fontSize: 11 }, axisLabel: { fontSize: 10, formatter: (v: number) => humanizeUsd(v) }, scale: true,
          },
          yAxis: {
            type: "value", name: "expected annual return", nameTextStyle: { fontSize: 11 },
            axisLabel: { fontSize: 10, formatter: (v: number) => humanizeUsd(v) }, scale: true,
          },
          series: [
            {
              type: "line", showSymbol: false, data: ordered.map((p) => [p.achieved_var99, p.expected_annual_return]),
              lineStyle: { color: c.neutral, type: "dashed" }, z: 1,
            },
            {
              type: "scatter", symbolSize: 20, z: 2,
              data: ordered.map((p) => ({
                name: themeFor(p.profile).label,
                value: [p.achieved_var99, p.expected_annual_return],
                itemStyle: { color: profileColor(p.profile, c) },
              })),
              label: { show: true, position: "top", fontSize: 10, formatter: (p: unknown) => (p as { data: { name: string } }).data.name },
            },
          ],
        };

        return (
          <div className="space-y-5">
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                Risk–return frontier
              </div>
              <CardBody>
                <EChart option={chart} height={320} />
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                  {ordered.map((p) => {
                    const t = themeFor(p.profile);
                    return (
                      <div key={p.profile} className="rounded border border p-2">
                        <div className={`text-xs font-semibold ${t.accent}`}>{t.label}</div>
                        <MetricGrid
                          columns={2}
                          metrics={[
                            { label: "Return/VaR", value: p.return_on_var.toFixed(2), tone: p.return_on_var >= 0 ? "gain" : "loss" },
                            { label: "Exp return", value: signedPct(p.expected_return_pct), tone: p.expected_return_pct >= 0 ? "gain" : "loss" },
                            { label: "Vol (ann)", value: humanizeUsd(p.portfolio_vol_annual) },
                            { label: "Leverage", value: `${p.gross_leverage.toFixed(2)}×` },
                          ]}
                        />
                      </div>
                    );
                  })}
                </div>
              </CardBody>
            </Card>

            <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
              {/* covariance / factor panel */}
              <Card>
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Trade covariance panel
                </div>
                <CardBody>
                  <MetricGrid
                    columns={2}
                    metrics={[
                      { label: "Trades", value: data.n_trades ?? "—" },
                      { label: "Risk factors", value: data.n_factors ?? "—" },
                      { label: "History", value: `${data.n_days ?? "—"}d` },
                      { label: "Capital base", value: humanizeUsd(data.capital_base) },
                    ]}
                  />
                  <p className="mt-3 text-micro text-fg-muted">
                    The optimiser sizes positions on an empirical trade-level covariance Σ built by projecting each
                    trade's sensitivities onto {data.n_factors} risk factors over {data.n_days} days — so sizes reflect
                    correlations, not just standalone vol.
                  </p>
                </CardBody>
              </Card>

              {/* expected-return derivation */}
              <Card className="lg:col-span-2">
                <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                  Expected-return drivers <span className="font-normal text-fg-muted">· per-trade carry / view</span>
                </div>
                <CardBody className="overflow-x-auto">
                  {erRows.length === 0 ? (
                    <div className="text-xs text-fg-secondary">No expected-return breakdown in this report.</div>
                  ) : (
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-micro uppercase tracking-wide text-fg-muted">
                          <th className="py-1.5 pr-3 font-medium">Trade</th>
                          <th className="py-1.5 pr-3 font-medium">Method</th>
                          <th className="py-1.5 pr-3 font-medium">Detail</th>
                          <th className="py-1.5 text-right font-medium">μ / yr</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border-subtle">
                        {erRows.slice(0, 14).map((r) => (
                          <tr key={r.trade_id} className="hover:bg-surface-2">
                            <td className="py-1.5 pr-3 font-mono text-xs text-fg-secondary">{r.trade_id.replace("TRADE_", "#")}</td>
                            <td className="py-1.5 pr-3 text-fg-secondary">{methodLabel(r.method)}</td>
                            <td className="py-1.5 pr-3 text-micro text-fg-muted">{r.detail}</td>
                            <td className={`py-1.5 text-right tnum ${r.mu_annual >= 0 ? "text-success-fg" : "text-danger-fg"}`} title={exactUsd(r.mu_annual)}>
                              {humanizeUsd(r.mu_annual)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardBody>
              </Card>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}
