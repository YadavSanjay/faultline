import { Link } from "react-router-dom";
import { useSignalCredit, type CreditImpliedRow } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { cn } from "@/lib/cn";
import { num, bp, pct, ddTone } from "./format";

// B3/B6 Implied Credit: option-implied credit across names, ranked by distance-to-default.
// The robust signal is the DD *ranking* (ORCL well below the mega-caps); the CreditGrades spread
// level is compressed by the synthetic-equity leverage, so it reads as an indicator, not a mark.

function Row({ r }: { r: CreditImpliedRow }) {
  return (
    <tr className="hover:bg-surface-2">
      <td className="px-4 py-2">
        <Link to={`/signals/${r.underlying}`} className="font-medium text-fg hover:underline">
          {r.underlying}
        </Link>
        <div className="text-micro text-fg-muted">{r.snapshot_date}</div>
      </td>
      <td className={cn("px-4 py-2 text-right tnum font-medium", ddTone(r.merton_dd))}>{num(r.merton_dd, 2)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(r.merton_pd, 2)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{bp(r.cg_spread_bp)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{bp(r.merton_spread_bp)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{bp(r.cds_reference_bp)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{bp(r.basis_bp)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{num(r.leverage, 2)}</td>
    </tr>
  );
}

export function CreditImplied() {
  const query = useSignalCredit();
  return (
    <QueryState query={query} empty={(d) => d.count === 0}>
      {(data) => (
        <div className="space-y-4">
          <div className="rounded-md border border bg-surface-2 px-4 py-2.5 text-xs leading-relaxed text-fg-secondary">
            <span className="font-medium text-fg">Option-implied credit.</span>{" "}
            A structural read of credit from the equity option surface — Merton distance-to-default / PD and the
            CreditGrades spread, with a single-name CDS cross-check where one exists. Ranked riskiest first (lowest
            DD). Read the <span className="font-medium">DD ranking</span> as the signal: the spread{" "}
            <span className="font-medium">level</span> is compressed by the synthetic-equity leverage (B3 caveat),
            so it is an indicator, never a pricing input.
          </div>
          <Card>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Underlier</th>
                    <th className="px-4 py-2 text-right font-medium">DD</th>
                    <th className="px-4 py-2 text-right font-medium">PD</th>
                    <th className="px-4 py-2 text-right font-medium">CreditGrades</th>
                    <th className="px-4 py-2 text-right font-medium">Merton</th>
                    <th className="px-4 py-2 text-right font-medium">CDS ref.</th>
                    <th className="px-4 py-2 text-right font-medium">Basis</th>
                    <th className="px-4 py-2 text-right font-medium">Leverage</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {data.rows.map((r) => (
                    <Row key={r.underlying} r={r} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </QueryState>
  );
}
