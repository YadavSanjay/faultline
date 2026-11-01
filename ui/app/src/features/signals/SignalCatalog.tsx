import { Link } from "react-router-dom";
import { useOptionSignals, type SignalSummary } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { cn } from "@/lib/cn";
import { pct, num, ddTone } from "./format";

// B6 Signal Catalog: one row per underlier with the latest option-implied read, linking to the
// per-name detail (vol term structure, skew, VRP, positioning, RND + tail probabilities).

function InvertedBadge({ inverted }: { inverted: boolean | null | undefined }) {
  if (inverted == null) return <span className="text-fg-muted">—</span>;
  return (
    <span
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-nano font-semibold uppercase tracking-wide",
        inverted ? "bg-warning-tint text-warning-fg" : "bg-surface-0 text-fg-secondary",
      )}
    >
      {inverted ? "inverted" : "normal"}
    </span>
  );
}

function Row({ s }: { s: SignalSummary }) {
  return (
    <tr className="hover:bg-surface-2">
      <td className="px-4 py-2">
        <Link to={`/signals/${s.underlying}`} className="font-medium text-fg hover:underline">
          {s.underlying}
        </Link>
        <div className="text-micro text-fg-muted">{s.snapshot_date}</div>
      </td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(s.atm_iv_30d)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{num(s.term_ratio, 2)}</td>
      <td className="px-4 py-2"><InvertedBadge inverted={s.term_inverted} /></td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(s.skew_25d_30d)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(s.vrp_20d)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{num(s.pc_oi_ratio, 2)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(s.expected_move_front)}</td>
      <td className="px-4 py-2 text-right tnum text-fg-secondary">{pct(s.prob_down_20)}</td>
      <td className={cn("px-4 py-2 text-right tnum font-medium", ddTone(s.merton_dd))}>
        {s.has_credit ? num(s.merton_dd, 1) : "—"}
      </td>
    </tr>
  );
}

export function SignalCatalog() {
  const query = useOptionSignals();
  return (
    <QueryState query={query} empty={(d) => d.count === 0}>
      {(data) => (
        <div className="space-y-4">
          <div className="rounded-md border border bg-surface-2 px-4 py-2.5 text-xs leading-relaxed text-fg-secondary">
            <span className="font-medium text-fg">What the option market is pricing.</span>{" "}
            Per underlier, the latest implied read: at-the-money vol, the 30d/90d{" "}
            <span className="font-medium">term ratio</span> (&gt;1 = inverted = near-term stress), 25-delta{" "}
            <span className="font-medium">skew</span>, the <span className="font-medium">variance-risk premium</span>{" "}
            (implied − realized), put/call OI <span className="font-medium">positioning</span>, the front-expiry{" "}
            <span className="font-medium">expected move</span>, the risk-neutral probability of a −20% move, and the
            option-implied <span className="font-medium">distance-to-default</span>. Derived by the marketdata
            signals runner (design workstream B); the same signals feed the scenario plausibility gate.
          </div>

          <Card>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                  <tr>
                    <th className="px-4 py-2 font-medium">Underlier</th>
                    <th className="px-4 py-2 text-right font-medium">ATM IV 30d</th>
                    <th className="px-4 py-2 text-right font-medium">Term ratio</th>
                    <th className="px-4 py-2 font-medium">Term</th>
                    <th className="px-4 py-2 text-right font-medium">Skew 25Δ</th>
                    <th className="px-4 py-2 text-right font-medium">VRP</th>
                    <th className="px-4 py-2 text-right font-medium">P/C OI</th>
                    <th className="px-4 py-2 text-right font-medium">Exp. move</th>
                    <th className="px-4 py-2 text-right font-medium">P(−20%)</th>
                    <th className="px-4 py-2 text-right font-medium">DD</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {data.names.map((s) => (
                    <Row key={s.underlying} s={s} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <p className="text-micro text-fg-muted">
            Snapshots on file: {data.snapshots.join(", ")}. Term ratio &gt; 1 and a rich put skew are the two
            signals that say the market is pricing near-term stress; DD is the robust option-implied credit
            signal (the CreditGrades spread level is compressed — see Implied Credit).
          </p>
        </div>
      )}
    </QueryState>
  );
}
