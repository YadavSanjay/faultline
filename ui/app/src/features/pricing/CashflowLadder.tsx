import type { Cashflow } from "@/lib/api";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// The cashflow ladder IS the pricing logic for linear instruments: each flow's
// date · accrual · rate · amount · discount factor · PV, with the column sum reconciling to
// the leg PV (shown explicitly with a ✓).

function pct(v: number | null | undefined) {
  return v == null ? "—" : `${(v * 100).toFixed(3)}%`;
}
function num(v: number | null | undefined, d = 4) {
  return v == null ? "—" : v.toFixed(d);
}

export function CashflowLadder({ cashflows, legPvs }: {
  cashflows: Cashflow[];
  legPvs: Record<string, number>;
}) {
  const legs = Array.from(new Set(cashflows.map((c) => c.leg)));
  const sumByLeg = (leg: string) =>
    cashflows.filter((c) => c.leg === leg).reduce((s, c) => s + (c.pv ?? 0), 0);

  return (
    <div className="space-y-3">
      <div className="overflow-auto rounded-md border border">
        <table className="w-full text-xs">
          <thead className="bg-surface-2 text-left text-fg-secondary">
            <tr>
              <th className="px-3 py-1.5 font-medium">Leg</th>
              <th className="px-3 py-1.5 font-medium">Pay date</th>
              <th className="px-3 py-1.5 text-right font-medium">Accrual</th>
              <th className="px-3 py-1.5 text-right font-medium">Rate</th>
              <th className="px-3 py-1.5 text-right font-medium">Amount</th>
              <th className="px-3 py-1.5 text-right font-medium">DF</th>
              <th className="px-3 py-1.5 text-right font-medium">PV</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-subtle">
            {cashflows.map((c, i) => (
              <tr key={`${c.leg}-${c.pay_date}-${i}`} className="hover:bg-surface-2">
                <td className="px-3 py-1.5">
                  <span className={cn("rounded px-1 text-nano", c.leg === "fixed" ? "bg-cat-indigo-tint text-cat-indigo-fg" : "bg-cat-teal-tint text-cat-teal-fg")}>
                    {c.leg}
                  </span>
                </td>
                <td className="px-3 py-1.5 tnum text-fg-secondary">{c.pay_date}</td>
                <td className="px-3 py-1.5 text-right tnum text-fg-secondary">{num(c.accrual)}</td>
                <td className="px-3 py-1.5 text-right tnum text-fg-secondary">{pct(c.rate)}</td>
                <td className="px-3 py-1.5 text-right tnum text-fg-secondary" title={exactUsd(c.amount ?? 0)}>
                  {humanizeUsd(c.amount)}
                </td>
                <td className="px-3 py-1.5 text-right tnum text-fg-secondary">{num(c.discount_factor, 5)}</td>
                <td className="px-3 py-1.5 text-right tnum font-medium text-fg" title={exactUsd(c.pv ?? 0)}>
                  {humanizeUsd(c.pv)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-4 text-xs">
        {legs.map((leg) => {
          const sum = sumByLeg(leg);
          const target = legPvs[leg];
          const ok = target == null || Math.abs(sum - target) < Math.max(1, Math.abs(target) * 1e-4);
          return (
            <div key={leg} className="rounded bg-surface-2 px-2 py-1">
              <span className="text-fg-muted">Σ {leg} PV</span>{" "}
              <span className="tnum font-medium text-fg">{humanizeUsd(sum)}</span>
              {target != null && (
                <span className={ok ? "ml-1 text-success-fg" : "ml-1 text-danger-fg"}>
                  {ok ? "✓ = leg PV" : "✗ ≠ leg PV"}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
