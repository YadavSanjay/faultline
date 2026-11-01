import type { ReactNode } from "react";
import { Card, CardBody } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { PROFILE_ORDER, themeFor } from "./format";

// Educative walk-through of the portfolio-construction methodology. Mirrors
// docs/design/portfolio_construction_design.md so users learn the model by exploring it.

function Section({ n, title, children }: { n: string; title: string; children: ReactNode }) {
  return (
    <Card>
      <div className="flex items-baseline gap-3 border-b border-subtle px-4 py-2.5">
        <span className="tnum text-xs font-semibold text-primary">{n}</span>
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
      </div>
      <CardBody className="space-y-3 text-sm leading-relaxed text-fg-secondary">{children}</CardBody>
    </Card>
  );
}

const AXES: { axis: string; balanced: string; aggressive: string; risky: string }[] = [
  { axis: "Risk budget (1-day 99% VaR / capital)", balanced: "1.0%", aggressive: "2.5%", risky: "5.0%" },
  { axis: "Gross leverage cap", balanced: "3.0× guardrail", aggressive: "5.0×", risky: "10.0×" },
  { axis: "Risk allocation", balanced: "mild tilt over equal-risk", aggressive: "Sharpe tilt", risky: "concentrated Sharpe" },
  { axis: "Concentration (risk / notional)", balanced: "15% / 10%", aggressive: "25% / 20%", risky: "45% / 35%" },
  { axis: "Hedge book carried", balanced: "100%", aggressive: "50%", risky: "15%" },
];

const STEPS = [
  {
    k: "STEP 1",
    h: "Trade-level risk model",
    p: "Each trade's first-order Greeks (DV01/key-rate, CS01, equity delta & vega per name, FX delta) are projected onto 250 days of aligned historical factor moves, producing a per-trade daily P&L series. Stacked, these form a 250×30 P&L matrix whose covariance is the empirically-diversified 30×30 trade Σ. Because first-order P&L is linear in size, resizing trade j by a multiplier just scales its column — so portfolio VaR for any resizing is one matrix multiply.",
    note: "Consistency: at current sizes the summed per-trade P&L equals the desk VaR engine to the dollar ($12.4mm 99% VaR).",
  },
  {
    k: "STEP 2",
    h: "Expected returns (hybrid)",
    p: "The return signal is carry/theta where economics are observable, an explicit view where they are not: bond carry (coupon − funding), swap/FX/CDS/xccy carry, option theta, and for equities & option delta an explicit β·ERP + growth tilt. Signs follow the book's P&L — a short's expected return is negative, a bought hedge's carry is a cost.",
    note: "These coefficients (ERP, betas, tilts, funding) are assumptions; they shape the tilt only, not the risk calibration.",
  },
  {
    k: "STEP 3",
    h: "Allocate & tilt",
    p: "Directional trades share the risk budget by target component-risk contributions: equal-risk (risk parity) as the diversified base, or Sharpe-tilted so risk concentrates in the best risk-adjusted-return names (the tilt power grows with appetite). Multipliers are solved by the standard convex risk-budgeting program (Spinu), with a concentration cap limiting any single trade's share.",
    note: null,
  },
  {
    k: "STEP 4",
    h: "Hedges by appetite",
    p: "Hedges are held at hedge_scale × their current size — full in balanced, thinned to 15% in risky — not scaled with the directional book. This deliberate choice stops a hedge inflating into a dominant directional short as the positions it offsets are resized. SMH-short is treated as directional (the short leg of the semis relative-value structure), and correctly shows a negative risk contribution.",
    note: null,
  },
  {
    k: "STEP 5",
    h: "Scale to the VaR budget",
    p: "The whole book is scaled by one scalar so its 1-day 99% VaR meets the mandate (a clean 1-D solve, since VaR is positively homogeneous). Two guardrails then apply in order — a per-trade notional cap, then a gross-leverage cap — and whichever binds first is reported.",
    note: null,
  },
];

export function Methodology() {
  return (
    <div className="max-w-3xl space-y-5">
      <Section n="00" title="The problem">
        <p>
          The book carries a flat ~$50mm gross per trade — the portfolio file's own notes call these
          &ldquo;representative placeholders.&rdquo; There is no reason NVDA is 240,000 shares or the 10Y payer swap
          is $50mm; the sizes encode no view on risk, correlation, or return.
        </p>
        <p>
          Portfolio construction replaces that with a repeatable process: pick a <strong>risk-appetite mandate</strong>,
          and let a transparent optimizer determine every position's size. The same 30 instruments, three different books.
        </p>
      </Section>

      <Section n="01" title="What a mandate is">
        <p>A mandate is expressed along five axes; each profile sets them differently.</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-body-sm">
            <thead>
              <tr className="text-micro uppercase tracking-wide text-fg-muted">
                <th className="py-1.5 pr-3 text-left font-medium">Axis</th>
                {PROFILE_ORDER.map((k) => (
                  <th key={k} className={cn("py-1.5 pr-3 text-right font-medium", themeFor(k).accent)}>
                    {themeFor(k).label.split(" ")[0]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {AXES.map((row) => (
                <tr key={row.axis}>
                  <td className="py-2 pr-3 text-fg-secondary">{row.axis}</td>
                  <td className="tnum py-2 pr-3 text-right text-fg">{row.balanced}</td>
                  <td className="tnum py-2 pr-3 text-right text-fg">{row.aggressive}</td>
                  <td className="tnum py-2 pr-3 text-right text-fg">{row.risky}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          The <strong>capital base</strong> (a fixed NAV) and the <strong>VaR budgets</strong> are the two primary dials;
          both live in <code className="rounded bg-surface-0 px-1 py-0.5 text-xs">configs/pricer/sizing.json</code> and
          should be set to the desk's real NAV and mandate.
        </p>
      </Section>

      <Section n="02" title="How each size is derived">
        <div className="space-y-3">
          {STEPS.map((s) => (
            <div key={s.k} className="rounded-md border border-subtle bg-surface-2/60 p-3">
              <div className="flex items-baseline gap-2">
                <span className="tnum text-micro font-semibold text-primary">{s.k}</span>
                <span className="text-sm font-semibold text-fg">{s.h}</span>
              </div>
              <p className="mt-1 text-sm text-fg-secondary">{s.p}</p>
              {s.note && <p className="mt-1 text-xs italic text-fg-muted">{s.note}</p>}
            </div>
          ))}
        </div>
      </Section>

      <Section n="03" title="Re-valuation: PV, Risk, Capital">
        <p>
          Once a profile's multipliers are solved, the book is re-valued under each configuration. Position scaling is
          <strong> exactly linear</strong> for a fixed market state (m identical contracts = m× the P&amp;L, gamma
          included), so PV and stress P&amp;L are the base per-trade values reweighted by the multipliers — no re-pricing
          per profile. Two capital lenses are reported: <strong>economic capital</strong> (97.5% ES of the reweighted
          book) and <strong>stress capital</strong> (worst loss across the standard stress scenarios).
        </p>
        <p className="text-xs text-fg-muted">
          A telling result: the worst-case scenario shifts from a credit blow-out (the hedged balanced book) to an
          AI-capex bust (the concentrated, hedge-light aggressive and risky books).
        </p>
      </Section>

      <Section n="04" title="Assumptions & limitations">
        <ul className="list-disc space-y-1.5 pl-5">
          <li>Two dials are placeholders — capital base ($1bn) and VaR budgets (1 / 2.5 / 5%). Set them to the desk's real NAV and mandate.</li>
          <li>Expected returns are assumption-driven and shape only the tilt; the risk engine (VaR, Σ) is market-calibrated.</li>
          <li>First-order risk: sizing uses sensitivity-based P&amp;L matching the desk VaR; option gamma is not re-optimized (it does scale linearly in the re-valuation).</li>
          <li>The θ proxy caps option decay at the premium and overstates decay for deep in-the-money options.</li>
        </ul>
        <p className="text-xs text-fg-muted">
          Full methodology: <code className="rounded bg-surface-0 px-1 py-0.5">docs/design/portfolio_construction_design.md</code>.
          Reproduce with <code className="rounded bg-surface-0 px-1 py-0.5">python -m src.pricer.sizing.sizing_runner</code>.
        </p>
      </Section>
    </div>
  );
}
