import { Link, useParams } from "react-router-dom";
import type { EChartsOption } from "echarts";
import { usePvHistory } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { EChart } from "@/components/shared/EChart";
import { humanizeUsd, exactUsd } from "@/lib/format";

// PR-B4 PV History: PV per valuation date for one trade (stored).
export function PvHistory() {
  const { tradeId = "" } = useParams();
  const query = usePvHistory(tradeId);

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
        <span>History</span>
      </nav>

      <QueryState query={query} empty={(d) => d.history.length === 0}>
        {(data) => {
          const option: EChartsOption = {
            grid: { left: 64, right: 24, top: 24, bottom: 28 },
            tooltip: { trigger: "axis" },
            xAxis: { type: "category", data: data.history.map((h) => h.valuation_date) },
            yAxis: { type: "value", scale: true, axisLabel: { formatter: (v: number) => humanizeUsd(v) } },
            series: [
              {
                name: "PV",
                type: "line",
                data: data.history.map((h) => h.pv),
                smooth: true,
                symbolSize: 7,
                areaStyle: { opacity: 0.08 },
              },
            ],
          };
          return (
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">
                PV history · {data.history.length} valuation date(s)
              </div>
              <CardBody>
                {data.history.length > 1 ? (
                  <EChart option={option} height={260} />
                ) : (
                  <div className="rounded-md bg-surface-2 px-3 py-2 text-xs text-fg-secondary">
                    Only one valuation date stored so far — the series grows as more runs land.
                  </div>
                )}
                <table className="mt-4 w-full text-sm">
                  <thead className="text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="py-1 font-medium">Date</th>
                      <th className="py-1 text-right font-medium">PV</th>
                      <th className="py-1 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {data.history.map((h) => (
                      <tr key={h.valuation_date}>
                        <td className="py-1 tnum text-fg-secondary">{h.valuation_date}</td>
                        <td className="py-1 text-right tnum text-fg" title={exactUsd(h.pv ?? 0)}>
                          {humanizeUsd(h.pv)}
                        </td>
                        <td className="py-1 text-fg-secondary">{h.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardBody>
            </Card>
          );
        }}
      </QueryState>
    </div>
  );
}
