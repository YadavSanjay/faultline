import { Link, useParams } from "react-router-dom";
import { useSignalDetail, useSignalRnd, useNewsEvents } from "@/lib/api";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { SeriesChart } from "@/components/shared/SeriesChart";
import { RndChart } from "./RndChart";
import { pct, num, bp, ddTone } from "./format";
import { cn } from "@/lib/cn";

function Stat({ label, value, tone, sub }: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="rounded-md border border-subtle bg-surface-1 px-3 py-2">
      <div className="text-nano uppercase tracking-wide text-fg-muted">{label}</div>
      <div className={cn("tnum mt-0.5 text-lg font-semibold tabular-nums", tone ?? "text-fg")}>{value}</div>
      {sub && <div className="text-micro text-fg-muted">{sub}</div>}
    </div>
  );
}

const n = (v: unknown): number | null => (typeof v === "number" ? v : null);

function NewsCard({ name }: { name: string }) {
  const q = useNewsEvents(name);
  const events = q.data?.events?.slice(0, 6) ?? [];
  if (!events.length) return null;
  return (
    <Card>
      <CardHeader title="Recent 8-K events" subtitle="SEC material-event filings (P3 news spine)" />
      <CardBody>
        <table className="w-full text-sm">
          <tbody className="divide-y divide-border-subtle">
            {events.map((e) => (
              <tr key={e.accession} className="hover:bg-surface-2">
                <td className="py-1.5 pr-3 tnum text-xs text-fg-secondary">{e.filing_date}</td>
                <td className="py-1.5 pr-3">
                  <div className="flex flex-wrap gap-1">
                    {e.items.map((it) => (
                      <span key={it.code} title={it.label}
                        className={cn("inline-block rounded px-1.5 py-0.5 text-nano font-semibold",
                          it.code === "5.02" ? "bg-danger-tint text-danger-fg" : "bg-surface-0 text-fg-secondary")}>
                        {it.code}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="py-1.5 text-xs text-fg-secondary">{e.items.map((it) => it.label).join("; ")}</td>
                <td className="py-1.5 text-right">
                  <a href={e.doc_url} target="_blank" rel="noreferrer"
                     className="font-mono text-micro text-primary hover:underline">SEC ↗</a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-micro text-fg-muted">
          Full stream + the news multiplier on this name's scenario shock: <Link to="/signals/news" className="text-primary hover:underline">News & Events</Link>.
        </p>
      </CardBody>
    </Card>
  );
}

export function SignalDetail() {
  const { name = "" } = useParams();
  const query = useSignalDetail(name);
  const rndQuery = useSignalRnd(name);

  return (
    <div>
      <div className="mb-4 flex items-center gap-2 text-sm text-fg-secondary">
        <Link to="/signals" className="hover:underline">Market Signals</Link>
        <span className="text-fg-muted">/</span>
        <span className="font-medium text-fg">{name}</span>
      </div>

      <QueryState query={query}>
        {(d) => {
          const l = d.latest;
          const c = d.credit_implied;
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
                <Stat label="ATM IV 30d" value={pct(n(l.atm_iv_30d))} sub={`90d ${pct(n(l.atm_iv_90d))}`} />
                <Stat
                  label="Term ratio"
                  value={num(n(l.term_ratio), 2)}
                  tone={n(l.term_ratio) != null && n(l.term_ratio)! > 1 ? "text-warning-fg" : "text-fg"}
                  sub={l.term_inverted ? "inverted" : "normal"}
                />
                <Stat label="Skew 25Δ" value={pct(n(l.skew_25d_30d))} sub="put − call IV" />
                <Stat
                  label="VRP 20d"
                  value={pct(n(l.vrp_20d))}
                  tone={n(l.vrp_20d) != null && n(l.vrp_20d)! < 0 ? "text-danger-fg" : "text-fg"}
                  sub="implied − realized"
                />
                <Stat label="Expected move" value={pct(n(l.expected_move_front))} sub={`${num(n(l.expected_move_front_dte), 0)}d`} />
                <Stat label="P/C OI" value={num(n(l.pc_oi_ratio), 2)} sub={`top-5 OI ${pct(n(l.oi_top5_share))}`} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-2">
                  <CardHeader
                    title="Risk-neutral density"
                    subtitle="Breeden-Litzenberger implied distribution at the front expiry — spot and ±expected-move marked"
                  />
                  <CardBody>
                    <QueryState query={rndQuery}>
                      {(r) => (
                        <>
                          <RndChart curve={r} />
                          <div className="mt-2 grid grid-cols-5 gap-2 text-center">
                            {[
                              ["P(−30%)", r.prob_down_30],
                              ["P(−20%)", r.prob_down_20],
                              ["P(−10%)", r.prob_down_10],
                              ["P(+10%)", r.prob_up_10],
                              ["P(+20%)", r.prob_up_20],
                            ].map(([lab, v]) => (
                              <div key={lab as string} className="rounded border border-subtle py-1">
                                <div className="text-nano text-fg-muted">{lab}</div>
                                <div className="tnum text-sm text-fg">{pct(n(v), 1)}</div>
                              </div>
                            ))}
                          </div>
                          <p className="mt-2 text-micro text-fg-muted">
                            Snapshot {r.snapshot_date} · spot {num(r.rnd_spot, 2)} · forward {num(r.rnd_forward, 2)} ·{" "}
                            {num(r.rnd_dte, 0)}d. These tail probabilities are what the scenario plausibility gate
                            grades an equity shock against.
                          </p>
                        </>
                      )}
                    </QueryState>
                    {!d.has_rnd && (
                      <div className="py-6 text-center text-sm text-fg-muted">No RND curve for {name}.</div>
                    )}
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader title="Option-implied credit" subtitle="Merton + CreditGrades (B3)" />
                  <CardBody className="space-y-2">
                    {c ? (
                      <>
                        <Stat
                          label="Distance to default"
                          value={num(c.merton_dd, 2)}
                          tone={ddTone(c.merton_dd)}
                          sub={`PD ${pct(c.merton_pd, 2)}`}
                        />
                        <div className="grid grid-cols-2 gap-2">
                          <Stat label="CreditGrades" value={bp(c.cg_spread_bp)} />
                          <Stat label="Merton spread" value={bp(c.merton_spread_bp)} />
                          <Stat label="CDS reference" value={bp(c.cds_reference_bp)} />
                          <Stat label="Basis" value={bp(c.basis_bp)} />
                        </div>
                        <p className="text-micro leading-relaxed text-fg-muted">
                          Leverage {num(c.leverage, 2)}. The robust signal is the DD ranking; the CreditGrades
                          spread level is compressed by the synthetic-equity leverage (documented B3 caveat) — an
                          indicator, never a pricing input.
                        </p>
                      </>
                    ) : (
                      <div className="py-6 text-center text-sm text-fg-muted">
                        No option-implied credit for {name}.
                      </div>
                    )}
                  </CardBody>
                </Card>
              </div>

              {d.history.length > 1 && (
                <Card>
                  <CardHeader title="Signal history" subtitle="Per snapshot on file" />
                  <CardBody>
                    <SeriesChart
                      rows={d.history as Record<string, unknown>[]}
                      dateCol="snapshot_date"
                      columns={["atm_iv_30d", "atm_iv_90d", "skew_25d_30d", "vrp_20d"]}
                      height={260}
                    />
                  </CardBody>
                </Card>
              )}

              <NewsCard name={name} />
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
