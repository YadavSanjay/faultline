import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  useMeasurement,
  type CoverageView,
  type DecisionView,
  type MeasurementUnavailable,
  type ReplayView,
} from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { MetricGrid, type Metric } from "@/components/shared/MetricGrid";
import { Zone } from "@/components/shared/Zone";
import { humanizeUsd, exactUsd, fmtTimestamp } from "@/lib/format";
import { cn } from "@/lib/cn";
import {
  diversityZone,
  fmtCosine,
  fmtGain,
  fmtShare,
  fmtSizeRatio,
  gainTone,
  missingLabel,
  sizeRatioTone,
  targetPosition,
} from "./measurementFormat";

// Measurement: how well the scenario set covers the book's risk (coverage), what the scenarios
// change in position sizing (decision impact), and how close the authored and enriched scenarios
// come to past market episodes (event replay). Reads GET /api/scenarios/measurement; the logic is
// in src/scenariogen/report_views.py.

const TH = "px-4 py-2 font-medium";
const TD = "px-4 py-1.5";

function runCaption(b: { run_id: string | null; stress_run_id: string | null; generated_at: string | null; tier: string | null }): string {
  const id = b.run_id ?? b.stress_run_id;
  const parts = [id ? `run ${id.slice(0, 8)}` : null, b.tier ? `tier ${b.tier}` : null, fmtTimestamp(b.generated_at)];
  return parts.filter(Boolean).join(" · ");
}

function Unavailable({ title, block }: { title: string; block: MeasurementUnavailable }) {
  return (
    <WarningBanner tone="info" title={`${title}: report not available`}>
      {block.reason}
    </WarningBanner>
  );
}

function Section({ title, subtitle, children }: { title: string; subtitle: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-base font-semibold text-fg">{title}</h2>
        <p className="text-xs text-fg-secondary">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

// --- coverage ------------------------------------------------------------- //
function DiversityMeter({ diversity }: { diversity: CoverageView["diversity"] }) {
  const pos = targetPosition(diversity);
  const label = pos.side === "in" ? "in target" : pos.side === "unknown" ? "target unknown" : `${pos.side} target`;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-fg-secondary">Participation ratio</span>
        <span className="tnum font-semibold text-fg">{diversity.participation_ratio?.toFixed(2) ?? "—"}</span>
        <Zone status={diversityZone(pos.side)} label={label} />
        {pos.lo != null && pos.hi != null && (
          <span className="text-xs text-fg-muted">target {pos.lo}–{pos.hi}</span>
        )}
      </div>
      {pos.valuePct != null && pos.bandPct != null && (
        <div className="relative h-3 w-full max-w-md rounded-full bg-surface-2" aria-hidden>
          <div
            className="absolute inset-y-0 rounded-full bg-success-tint"
            style={{ left: `${pos.bandPct.from}%`, width: `${pos.bandPct.to - pos.bandPct.from}%` }}
          />
          <div className="absolute inset-y-[-3px] w-0.5 rounded bg-fg" style={{ left: `${pos.valuePct}%` }} />
        </div>
      )}
      <p className="text-micro text-fg-muted">
        How many independent loss patterns the scenarios form (1 = all scenarios hit the book the same way).
      </p>
    </div>
  );
}

function CoverageSection({ c }: { c: CoverageView }) {
  const maxLoss = Math.max(0, ...c.losses.map((l) => Math.abs(l.loss)));
  const metrics: Metric[] = [
    { label: "Coverage", value: fmtShare(c.coverage), caption: "risk variance the scenarios span" },
    { label: "Directions", value: c.n_directions ?? c.directions.length, caption: `${c.directions.filter((d) => d.uncovered).length} uncovered` },
    { label: "Uncovered share", value: fmtShare(c.uncovered_share), caption: "variance in uncovered directions", tone: c.uncovered_share > 0 ? "loss" : "neutral" },
    { label: "Merge candidates", value: c.merge_pairs.length, caption: `${c.missing.length} scenario(s) missing` },
  ];
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="space-y-3">
          <MetricGrid metrics={metrics} columns={4} />
          <div className="text-xs text-fg-muted">{runCaption(c)}</div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader title="Risk directions" subtitle="principal directions of the book's P&L risk, largest first — spanned share is how much of each the scenarios reach" />
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                <tr>
                  <th className={TH}>#</th>
                  <th className={cn(TH, "text-right")}>Variance share</th>
                  <th className={cn(TH, "text-right")}>Spanned</th>
                  <th className={TH}>Status</th>
                  <th className={TH}>Top loadings</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {c.directions.map((d) => (
                  <tr key={d.index} className="hover:bg-surface-2">
                    <td className={cn(TD, "tnum text-xs text-fg")}>{d.index}</td>
                    <td className={cn(TD, "text-right tnum text-xs text-fg-secondary")}>{fmtShare(d.variance_share)}</td>
                    <td className={cn(TD, "text-right tnum text-xs text-fg-secondary")}>{fmtShare(d.spanned_share)}</td>
                    <td className={TD}>
                      {d.uncovered ? <Zone status="amber" label="uncovered" /> : <Zone status="green" label="covered" />}
                    </td>
                    <td className={cn(TD, "font-mono text-micro text-fg-secondary")}>
                      {d.top_loadings.length === 0
                        ? <span className="text-fg-muted">—</span>
                        : d.top_loadings.slice(0, 3).map((l) => `${l.trade_id ?? "?"} ${l.loading != null ? l.loading.toFixed(2) : "—"}`).join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title="Diversity" subtitle="participation ratio of the scenario losses against its target band" />
            <CardBody><DiversityMeter diversity={c.diversity} /></CardBody>
          </Card>
          <Card>
            <CardHeader title="Merge candidates" subtitle="scenario pairs whose P&L vectors are nearly parallel" />
            {c.merge_pairs.length === 0 ? (
              <CardBody className="text-xs text-fg-muted">none</CardBody>
            ) : (
              <ul className="divide-y divide-border-subtle text-sm">
                {c.merge_pairs.map((p) => (
                  <li key={`${p.a}|${p.b}`} className="flex items-center justify-between gap-2 px-4 py-2">
                    <span className="text-fg">{p.a} <span className="text-fg-muted">↔</span> {p.b}</span>
                    <span className="tnum text-xs text-fg-secondary">cos {fmtCosine(p.cosine)}</span>
                  </li>
                ))}
              </ul>
            )}
            {c.missing.length > 0 && (
              <CardBody className="border-t border-subtle text-xs text-fg-secondary">
                <span className="font-medium text-warning-fg">Missing:</span> {c.missing.map(missingLabel).join("; ")}
              </CardBody>
            )}
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader title="Scenario losses" subtitle="book loss under each scenario, largest first" />
        {c.losses.length === 0 ? (
          <CardBody className="text-xs text-fg-muted">none recorded</CardBody>
        ) : (
          <CardBody className="space-y-1.5">
            {c.losses.map((l) => (
              <div key={l.scenario} className="grid grid-cols-[minmax(8rem,14rem)_1fr_6rem] items-center gap-3 text-xs">
                <Link to={`/scenarios/${l.scenario}`} className="truncate text-fg hover:underline">{l.scenario}</Link>
                <div className="h-2 rounded-full bg-surface-2">
                  <div className="h-2 rounded-full bg-danger/60" style={{ width: `${maxLoss > 0 ? (Math.abs(l.loss) / maxLoss) * 100 : 0}%` }} />
                </div>
                <span className="text-right tnum text-fg-secondary" title={exactUsd(l.loss)}>{humanizeUsd(l.loss)}</span>
              </div>
            ))}
          </CardBody>
        )}
      </Card>
    </div>
  );
}

// --- decision impact ------------------------------------------------------ //
function Binding({ from, to }: { from: string | null; to: string | null }) {
  if (from === to) return <span className="font-mono text-micro text-fg-muted">{from ?? "—"}</span>;
  return (
    <span className="font-mono text-micro">
      <span className="text-fg-secondary">{from ?? "—"}</span>
      <span className="text-fg-muted"> → </span>
      <span className="font-semibold text-warning-fg">{to ?? "—"}</span>
    </span>
  );
}

function DecisionSection({ d }: { d: DecisionView }) {
  const books = Array.from(new Set(d.without.flatMap((w) => w.books.map((b) => b.book))));
  const order = d.books.map((b) => b.book);
  books.sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    if (ia !== -1 || ib !== -1) return (ia === -1 ? 1e9 : ia) - (ib === -1 ? 1e9 : ib);
    return a.localeCompare(b);
  });
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Stress constraint by book"
          subtitle="worst scenario before the stress limit, and the constraint that binds the sizing without → with it"
          right={<span className="text-xs text-fg-muted">{runCaption(d)}</span>}
        />
        {d.books.length === 0 ? (
          <CardBody className="text-xs text-fg-muted">no stress set in this report</CardBody>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                <tr>
                  <th className={TH}>Book</th>
                  <th className={cn(TH, "text-right")}>Stress budget</th>
                  <th className={TH}>Worst scenario</th>
                  <th className={cn(TH, "text-right")}>Worst loss</th>
                  <th className={cn(TH, "text-right")}>Over budget</th>
                  <th className={TH}>Binding</th>
                  <th className={cn(TH, "text-right")}>Trades resized</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {d.books.map((b) => {
                  const over = b.worst_loss != null && b.stress_budget != null && b.worst_loss > b.stress_budget;
                  return (
                    <tr key={b.book} className="hover:bg-surface-2">
                      <td className={cn(TD, "text-xs font-medium text-fg")}>{b.book}</td>
                      <td className={cn(TD, "text-right tnum text-xs text-fg-secondary")} title={exactUsd(b.stress_budget)}>{humanizeUsd(b.stress_budget)}</td>
                      <td className={cn(TD, "text-xs text-fg-secondary")}>{b.worst_scenario ?? "—"}</td>
                      <td className={cn(TD, "text-right tnum text-xs", over ? "text-danger-fg" : "text-fg-secondary")} title={exactUsd(b.worst_loss)}>{humanizeUsd(b.worst_loss)}</td>
                      <td className={cn(TD, "text-right tnum text-xs", b.n_over_budget > 0 ? "text-warning-fg" : "text-fg-muted")}>{b.n_over_budget} scen.</td>
                      <td className={TD}><Binding from={b.binding_from} to={b.binding_to} /></td>
                      <td className={cn(TD, "text-right tnum text-xs text-fg-secondary")}>{b.n_trades_changed ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Without each source"
          subtitle="sizing re-run with one enrichment source removed: binding constraint and mean size ratio (without ÷ with) per book"
        />
        {d.without.length === 0 ? (
          <CardBody className="text-xs text-fg-muted">no per-source comparison in this report</CardBody>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                <tr>
                  <th className={TH}>Without</th>
                  {books.map((b) => <th key={b} className={TH}>{b}</th>)}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {d.without.map((w) => {
                  const byBook = new Map(w.books.map((b) => [b.book, b]));
                  return (
                    <tr key={w.source} className="hover:bg-surface-2">
                      <td className={cn(TD, "font-mono text-xs text-fg")}>{w.source}</td>
                      {books.map((name) => {
                        const b = byBook.get(name);
                        return (
                          <td key={name} className={cn(TD, "align-top")}>
                            {b ? (
                              <div className="space-y-0.5">
                                <div className={cn("tnum text-xs font-medium", sizeRatioTone(b.size_ratio))}>{fmtSizeRatio(b.size_ratio)}</div>
                                {b.binding_changed
                                  ? <Binding from={b.binding_from} to={b.binding_to} />
                                  : <div className="text-micro text-fg-muted">binding unchanged</div>}
                              </div>
                            ) : <span className="text-xs text-fg-muted">—</span>}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

// --- event replay --------------------------------------------------------- //
function Nearest({ label, v }: { label: string; v: ReplayView["episodes"][number]["authored"] }) {
  return (
    <div className="text-xs">
      <span className="text-fg-muted">{label} </span>
      {v.nearest ? (
        <Link to={`/scenarios/${v.nearest}`} className="text-fg hover:underline">{v.nearest}</Link>
      ) : <span className="text-fg-muted">no scenario</span>}
      <span className="tnum text-fg-secondary"> · cos {fmtCosine(v.cosine)}</span>
    </div>
  );
}

function ReplaySection({ r }: { r: ReplayView }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Episodes"
          subtitle="closest scenario to each past market move, authored vs enriched — gain is the cosine improvement"
          right={<span className="text-xs text-fg-muted">{runCaption(r)}</span>}
        />
        {r.episodes.length === 0 ? (
          <CardBody className="text-xs text-fg-muted">no episodes</CardBody>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                <tr>
                  <th className={TH}>Episode</th>
                  <th className={TH}>Closest scenario</th>
                  <th className={cn(TH, "text-right")}>Gain</th>
                  <th className={TH}>Scenarios</th>
                  <th className={TH}>Point in time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {r.episodes.map((e) => (
                  <tr key={e.name} className="align-top hover:bg-surface-2">
                    <td className={TD}>
                      <div className="text-xs font-medium text-fg">{e.name}</div>
                      <div className="text-micro text-fg-muted">as of {e.as_of ?? "—"} · {e.horizon_days ?? "—"}d</div>
                    </td>
                    <td className={cn(TD, "space-y-0.5")}>
                      <Nearest label="authored" v={e.authored} />
                      <Nearest label="enriched" v={e.enriched} />
                    </td>
                    <td className={cn(TD, "text-right tnum text-xs font-medium", gainTone(e.gain))}>{fmtGain(e.gain)}</td>
                    <td className={cn(TD, "text-xs text-fg-secondary")}>
                      <div>{(e.available ?? []).length} compared</div>
                      {e.dropped.length > 0 && (
                        <div className="text-micro text-fg-muted" title={e.dropped.join(", ")}>
                          {e.dropped.length} dropped: {Array.from(new Set(e.dropped)).join(", ")}
                        </div>
                      )}
                    </td>
                    <td className={cn(TD, "text-micro text-fg-secondary")}>
                      <div>regime as of {e.regime_asof ?? "—"}</div>
                      {e.dropped_issuer_factors.length > 0 && (
                        <div className="text-fg-muted">excluded issuer factors: {e.dropped_issuer_factors.join(", ")}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {r.limitations.length > 0 && (
        <WarningBanner tone="info" title="Limitations">
          <ul className="list-disc space-y-0.5 pl-4">
            {r.limitations.map((l, i) => <li key={i}>{l}</li>)}
          </ul>
        </WarningBanner>
      )}
    </div>
  );
}

export function Measurement() {
  const [valuationDate, setValuationDate] = useState<string | undefined>(undefined);
  const query = useMeasurement(valuationDate);
  return (
    <QueryState query={query}>
      {(data) => (
        <div className="space-y-8">
          <div className="flex flex-wrap items-center gap-2 text-xs text-fg-secondary">
            <span>Valuation date</span>
            {data.available_dates.length > 1 ? (
              <select
                className="rounded border border-strong bg-surface-1 px-2 py-1 text-fg"
                value={data.valuation_date ?? ""}
                onChange={(e) => setValuationDate(e.target.value)}
              >
                {data.available_dates.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            ) : (
              <span className="tnum text-fg">{data.valuation_date ?? "—"}</span>
            )}
            <span className="text-fg-muted">· coverage and decision impact are per date; the event replay is the newest run overall</span>
          </div>

          <Section title="Coverage" subtitle="how much of the book's risk the scenario set reaches, and how distinct the scenarios are">
            {data.coverage.available
              ? <CoverageSection c={data.coverage} />
              : <Unavailable title="Coverage" block={data.coverage} />}
          </Section>

          <Section title="Decision impact" subtitle="what the stress scenarios change in position sizing, and what each enrichment source contributes">
            {data.decision.available
              ? <DecisionSection d={data.decision} />
              : <Unavailable title="Decision impact" block={data.decision} />}
          </Section>

          <Section title="Event replay" subtitle="does enrichment bring the scenarios closer to real past market moves?">
            {data.replay.available
              ? <ReplaySection r={data.replay} />
              : <Unavailable title="Event replay" block={data.replay} />}
          </Section>
        </div>
      )}
    </QueryState>
  );
}
