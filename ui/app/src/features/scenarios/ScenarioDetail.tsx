import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useAblationScenario, useScenarioDetail, useScenarioResults, useCorrelation, useScenarioNarrative, useScenarioAttribution, useScenarioTrace, useScenarioTraceCall, type ScenarioDetailResponse, type ScenarioResultsResponse, type ScenarioNarrativeResponse, type AttributionResponse, type ServiceContribution, type TraceResponse, type TraceCallResponse } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { Heatmap } from "@/components/shared/Heatmap";
import { Markdown } from "@/components/shared/Markdown";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { categoryClass } from "@/components/shared/CategoryTag";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { fmtMove, fmtMultiplier, fmtRawMove, ORIGIN_STYLE, plausibilityLabel, plausibilityZone, signoffZone } from "./format";
import { cn } from "@/lib/cn";
import { AblationWaterfall } from "./AblationWaterfall";
import { NotRunDetail, ReplayWindowCard } from "./ScenarioDefinition";
import { UnderTest } from "./UnderTest";

type Tab = "derivation" | "layers" | "valueadd" | "results" | "plausibility" | "trace" | "narrative";

const TAB_LABEL: Record<Tab, string> = {
  derivation: "Shock Derivation",
  layers: "Layers",
  valueadd: "Service Value-Add",
  results: "Results",
  plausibility: "Plausibility",
  trace: "Calls / Trace",
  narrative: "AI Narrative",
};


const STATUS_STYLE: Record<string, { bg: string; label: string }> = {
  applied: { bg: "bg-success-tint text-success-fg", label: "applied" },
  gate_failed: { bg: "bg-warning-tint text-warning-fg", label: "gate failed" },
  unavailable: { bg: "bg-danger-tint text-danger-fg", label: "unavailable" },
  not_configured: { bg: "bg-surface-0 text-fg-secondary", label: "not used" },
};

function fmtLatency(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function ShockDerivation({ d }: { d: ScenarioDetailResponse }) {
  const corr = useCorrelation();
  const driverKeys = Object.keys(d.drivers ?? {});
  const vector = d.vector ?? [];

  return (
    <div className="space-y-4">
      <div className="rounded-md border-l-2 border-primary bg-primary-tint px-4 py-2.5 text-sm text-fg-secondary">
        You asserted {driverKeys.length} driver(s); conditional-covariance propagation over Σ ({d.covariance?.window},{" "}
        {d.covariance?.n_obs} days) fills the other {vector.length - driverKeys.length} factors with market-consistent
        co-moves, then {Object.keys(d.overlays ?? {}).length} overlay(s) are added on top.
      </div>

      {/* flow: drivers -> propagation -> overlays */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader title="① Drivers" subtitle="narrative-set (× severity)" />
          <CardBody className="space-y-1">
            {Object.entries(d.drivers ?? {}).map(([f, m]) => (
              <div key={f} className="flex justify-between text-sm">
                <span className="font-mono text-fg-secondary">{f}</span>
                <span className="tnum font-medium text-cat-indigo-fg">{fmtMove(m)}</span>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="② Propagation (Σ)" subtitle={`conditional covariance · cov ${d.covariance?.cov_id?.slice(0, 8)}`} />
          <CardBody className="text-xs text-fg-secondary">
            Gaussian conditional mean over the correlation matrix below — every other factor moves because it
            historically co-moves with the drivers.
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="③ Overlays" subtitle="idiosyncratic add-ons" />
          <CardBody className="space-y-1">
            {Object.entries(d.overlays ?? {}).length === 0 && <span className="text-xs text-fg-muted">none</span>}
            {Object.entries(d.overlays ?? {}).map(([f, m]) => (
              <div key={f} className="flex justify-between text-sm">
                <span className="font-mono text-fg-secondary">{f}</span>
                <span className="tnum font-medium text-warning-fg">{fmtMove(m)}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>

      {d.replay && <ReplayWindowCard replay={d.replay} />}

      {/* correlation heatmap */}
      <Card>
        <CardHeader title="Correlation (Σ)" subtitle="why each factor moves — drivers marked ▸" />
        <CardBody>
          <QueryState query={corr} empty={(c) => !c.available}>
            {(c) => <Heatmap labels={c.factors} matrix={c.correlation} highlight={driverKeys} />}
          </QueryState>
        </CardBody>
      </Card>

      {/* materialized vector */}
      <Card>
        <CardHeader title="④ Materialized factor vector" subtitle={`${vector.length} factors, origin-tagged`} />
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
              <tr>
                <th className="px-4 py-2 font-medium">Factor</th>
                <th className="px-4 py-2 text-right font-medium">Move</th>
                <th className="px-4 py-2 font-medium">Origin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {vector.map((v) => {
                const st = ORIGIN_STYLE[v.origin] ?? ORIGIN_STYLE.derived;
                return (
                  <tr key={v.factor} className="hover:bg-surface-2">
                    <td className="px-4 py-1.5 font-mono text-xs text-fg">{v.factor}</td>
                    <td className={cn("px-4 py-1.5 text-right tnum", v.display >= 0 ? "text-success-fg" : "text-danger-fg")}>
                      {v.display > 0 ? "+" : ""}{v.display}{v.unit}
                    </td>
                    <td className="px-4 py-1.5">
                      <span className={cn("rounded px-1.5 py-0.5 text-nano", st.bg)}>{st.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* macro provenance */}
      {d.macro?.used_macro ? (
        <Card>
          <CardHeader title="MacroRAG provenance" subtitle={`world view ${d.macro.world_view_id}`} />
          <CardBody className="text-xs text-fg-secondary">{d.macro.summary}</CardBody>
        </Card>
      ) : d.source === "dualrag" ? (
        <div className="text-xs text-fg-muted">
          MacroRAG legs weren’t applied this run (synthesis gate-failed or unavailable) — the macro legs fell back to
          authored. See the <span className="font-medium text-fg-secondary">Service Value-Add</span> tab for exactly what
          MacroRAG and ARag each contributed.
        </div>
      ) : (
        <div className="text-xs text-fg-muted">Drivers are static (authored config) — MacroRAG not used for this scenario.</div>
      )}
    </div>
  );
}

function Results({ name, severity }: { name: string; severity: string }) {
  const query = useScenarioResults(name, severity);
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(r: ScenarioResultsResponse) => {
        const byAc = Object.entries(r.pnl_by_asset_class ?? {}).sort((a, b) => a[1] - b[1]);
        const maxAbs = Math.max(...byAc.map(([, v]) => Math.abs(v)), 1);
        const cap = r.capital;
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Total P&L" subtitle="full revaluation" />
                <CardBody>
                  <div className={cn("tnum text-3xl font-semibold", (r.total_pnl ?? 0) >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(r.total_pnl ?? 0)}>
                    {humanizeUsd(r.total_pnl)}
                  </div>
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Capital (ES-under-shock)" subtitle={cap ? `FRTB ${(cap.level * 100).toFixed(1)}% ES · ${cap.lookback_days}d` : ""} />
                <CardBody>
                  {cap ? (
                    <div className="flex items-end gap-6">
                      <div>
                        <div className="text-micro uppercase text-fg-muted">ES base</div>
                        <div className="tnum text-fg">{humanizeUsd(cap.es_base)}</div>
                      </div>
                      <div>
                        <div className="text-micro uppercase text-fg-muted">ES shocked</div>
                        <div className="tnum text-fg">{humanizeUsd(cap.es_shocked)}</div>
                      </div>
                      <div>
                        <div className="text-micro uppercase text-fg-muted">capital Δ</div>
                        <div className="tnum text-lg font-semibold text-fg" title={exactUsd(cap.capital_delta)}>{humanizeUsd(cap.capital_delta)}</div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-sm text-fg-muted">not computed in this run (run with capital)</div>
                  )}
                </CardBody>
              </Card>
            </div>

            <Card>
              <CardHeader title="P&L by asset class" />
              <CardBody className="space-y-1.5">
                {byAc.map(([ac, v]) => (
                  <div key={ac} className="flex items-center gap-3">
                    <span className="w-20 shrink-0 text-xs capitalize text-fg-secondary">{ac}</span>
                    <div className="relative h-4 flex-1 rounded bg-surface-0">
                      <div className={cn("absolute top-0 h-4 rounded", v >= 0 ? "bg-success" : "bg-danger")} style={{ width: `${(Math.abs(v) / maxAbs) * 100}%` }} />
                    </div>
                    <span className={cn("tnum w-24 shrink-0 text-right text-xs", v >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(v)}>{humanizeUsd(v)}</span>
                  </div>
                ))}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Per-trade P&L" />
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-4 py-2 font-medium">Trade</th>
                      <th className="px-4 py-2 font-medium">Asset class</th>
                      <th className="px-4 py-2 text-right font-medium">Base PV</th>
                      <th className="px-4 py-2 text-right font-medium">Scenario PV</th>
                      <th className="px-4 py-2 text-right font-medium">P&L</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {[...(r.trades ?? [])].sort((a, b) => a.pnl - b.pnl).map((t) => (
                      <tr key={t.trade_id} className="hover:bg-surface-2">
                        <td className="px-4 py-1.5">
                          <Link to={`/pricing/trade/${t.trade_id}`} className="font-mono text-fg hover:underline">{t.trade_id}</Link>
                        </td>
                        <td className="px-4 py-1.5 capitalize text-fg-secondary">{t.asset_class}</td>
                        <td className="px-4 py-1.5 text-right tnum text-fg-secondary">{humanizeUsd(t.base_pv)}</td>
                        <td className="px-4 py-1.5 text-right tnum text-fg-secondary">{humanizeUsd(t.scenario_pv)}</td>
                        <td className={cn("px-4 py-1.5 text-right tnum font-medium", t.pnl >= 0 ? "text-success-fg" : "text-danger-fg")} title={exactUsd(t.pnl)}>{humanizeUsd(t.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}

function PlausibilityView({ d }: { d: ScenarioDetailResponse }) {
  const p = d.plausibility;
  if (!p) return <div className="text-sm text-fg-muted">no plausibility result</div>;
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-center gap-3">
          <Zone status={plausibilityZone(p.status)} label={plausibilityLabel(p.status)} />
          <span className="text-sm text-fg-secondary">{p.promotable ? "promotable — included in the run" : "held out of the run"}</span>
        </div>
        {p.findings.length === 0 ? (
          <div className="text-sm text-fg-secondary">All factor moves within bounds and the historical envelope.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-fg-secondary">
              <tr>
                <th className="py-1 font-medium">Factor</th>
                <th className="py-1 font-medium">Check</th>
                <th className="py-1 text-right font-medium">Observed</th>
                <th className="py-1 text-right font-medium">Limit</th>
                <th className="py-1 font-medium">Severity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {p.findings.map((f, i) => (
                <tr key={`${f.factor}-${i}`}>
                  <td className="py-1 font-mono text-xs text-fg">{f.factor}</td>
                  <td className="py-1 text-fg-secondary">{f.check}</td>
                  <td className="py-1 text-right tnum text-fg-secondary">{f.observed}</td>
                  <td className="py-1 text-right tnum text-fg-secondary">{f.limit}</td>
                  <td className="py-1"><span className={f.severity === "fail" ? "text-danger-fg" : "text-warning-fg"}>{f.severity}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="text-micro text-fg-muted">Bound = hard cap per factor class (→ fail). Envelope = n-sigma historical band (stress moves are designed to exceed it → flag).</div>
      </CardBody>
    </Card>
  );
}

function AiProvenance({ d }: { d: ScenarioDetailResponse }) {
  const llm = d.llm;
  if (!llm) return null;
  const models = Object.entries(llm.models ?? {}).map(([s, m]) => `${s}:${m}`).join(", ");
  return (
    <Card>
      <CardHeader title="AI generation" subtitle={`${llm.mode ?? "llm"} · world view ${llm.world_view_id || "—"}`} />
      <CardBody className="space-y-2 text-xs text-fg-secondary">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span>models: <span className="font-mono">{models || "—"}</span></span>
          {llm.total_cost_usd != null && <span>cost: <span className="tnum">${llm.total_cost_usd.toFixed(4)}</span></span>}
          <span>{llm.n_llm_calls ?? 0} LLM calls · {llm.n_tool_calls ?? 0} tool calls</span>
        </div>
        {llm.legs && llm.legs.length > 0 && (
          <div className="space-y-1">
            <div className="text-micro uppercase text-fg-muted">driver legs · origin · citations</div>
            <div className="flex flex-wrap gap-1">
              {llm.legs.map((leg, i) => {
                const st = ORIGIN_STYLE[leg.origin] ?? ORIGIN_STYLE.derived;
                return (
                  <span key={`${leg.factor}-${i}`} className={cn("rounded px-1.5 py-0.5 text-nano", st.bg)}>
                    <span className="font-mono">{leg.factor}</span> · {leg.n_citations} cite{leg.n_citations === 1 ? "" : "s"}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="text-micro text-fg-muted">Generated by the LLM scenario flow; the full narrative is in the “AI Narrative” tab.</div>
      </CardBody>
    </Card>
  );
}

function Narrative({ name, severity }: { name: string; severity: string }) {
  const query = useScenarioNarrative(name, severity);
  return (
    <QueryState query={query} empty={(d: ScenarioNarrativeResponse) => !d.available}>
      {(n: ScenarioNarrativeResponse) => (
        <Card>
          <CardHeader
            title="AI narrative"
            subtitle={n.cost ? `generation cost $${(n.cost.total_cost_usd ?? 0).toFixed(4)} · ${n.cost.n_llm_calls ?? 0} LLM calls · ${n.cost.n_tool_calls ?? 0} tool calls` : ""}
          />
          <CardBody>
            <Markdown source={n.markdown ?? ""} />
          </CardBody>
        </Card>
      )}
    </QueryState>
  );
}

function ServiceCard({ c, title }: { c: ServiceContribution; title: string }) {
  const st = STATUS_STYLE[c.status] ?? STATUS_STYLE.not_configured;
  const calibrated = c.legs.filter((l) => l.detail?.calibration?.applied);
  return (
    <Card>
      <CardHeader title={title} subtitle={c.world_view_id ? `world view ${c.world_view_id}` : undefined} />
      <CardBody className="space-y-2">
        <div className="flex items-center gap-2">
          <span className={cn("rounded px-1.5 py-0.5 text-nano font-semibold", st.bg)}>{st.label}</span>
          <span className="text-xs text-fg-secondary">{c.n_legs} leg(s) · {c.n_citations} citation(s)</span>
        </div>
        {c.status === "gate_failed" && (c.gate_findings?.length ?? 0) > 0 && (
          <ul className="space-y-0.5 text-micro text-warning-fg">
            {c.gate_findings!.slice(0, 4).map((f, i) => <li key={i}>• {f}</li>)}
            {c.gate_findings!.length > 4 && <li className="text-fg-muted">+{c.gate_findings!.length - 4} more…</li>}
          </ul>
        )}
        {c.legs.length > 0 && (
          <table className="w-full text-xs">
            <thead className="text-left text-nano uppercase text-fg-muted">
              <tr>
                <th className="py-1 font-medium">Factor</th>
                <th className="py-1 font-medium">Role</th>
                <th className="py-1 text-right font-medium">Final</th>
                <th className="py-1 text-right font-medium">Δ authored</th>
                <th className="py-1 text-right font-medium">Cites</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {c.legs.map((leg, i) => (
                <tr key={`${leg.factor}-${i}`}>
                  <td className="py-1 font-mono text-fg">{leg.factor}</td>
                  <td className="py-1 text-fg-secondary">{leg.role}</td>
                  <td className="py-1 text-right tnum text-fg">{fmtRawMove(leg.factor, leg.final_move)}</td>
                  <td className="py-1 text-right tnum text-fg-secondary">{leg.delta == null ? "new" : fmtRawMove(leg.factor, leg.delta)}</td>
                  <td className="py-1 text-right tnum text-fg-secondary">{leg.n_citations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {calibrated.length > 0 && (
          <div className="space-y-0.5 rounded bg-surface-2 px-2 py-1.5 text-micro text-fg-secondary">
            {calibrated.map((l, i) => {
              const cal = l.detail.calibration!;
              const exp = cal.exposure != null ? `${(cal.exposure * 100).toFixed(0)}%` : "?";
              return (
                <div key={i}>
                  <span className="font-mono">{l.factor}</span> sized {fmtMultiplier(cal.multiplier)} on {exp} {cal.concept} share
                </div>
              );
            })}
          </div>
        )}
        {c.summary && <div className="text-micro text-fg-muted">{c.summary}</div>}
      </CardBody>
    </Card>
  );
}

function ServiceValueAdd({ name }: { name: string }) {
  const query = useScenarioAttribution(name);
  return (
    <QueryState query={query} empty={(d: AttributionResponse) => !d.available}>
      {(d: AttributionResponse) => {
        const a = d.attribution!;
        return (
          <div className="space-y-4">
            <Card>
              <CardHeader title="What each service added" subtitle="MacroRAG + ARag vs the authored baseline" />
              <CardBody>
                <ul className="space-y-1 text-sm text-fg">
                  {a.headline_lines.map((ln, i) => (
                    <li key={i} className="flex gap-2"><span className="text-fg-muted">›</span><span>{ln}</span></li>
                  ))}
                </ul>
              </CardBody>
            </Card>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <ServiceCard c={a.macrorag} title="MacroRAG — macro legs" />
              <ServiceCard c={a.arag} title="ARag — company legs (SEC 10-K/Q)" />
            </div>
            {a.narrative?.text && (
              <Card>
                <CardHeader title="Value-add narrative" subtitle={`${a.narrative.model} · $${a.narrative.cost_usd.toFixed(4)}`} />
                <CardBody><Markdown source={a.narrative.text} /></CardBody>
              </Card>
            )}
          </div>
        );
      }}
    </QueryState>
  );
}

function CallDetail({ name, seq }: { name: string; seq: number }) {
  const query = useScenarioTraceCall(name, seq);
  const Block = ({ label, body }: { label: string; body?: string }) =>
    body ? (
      <div className="space-y-1">
        <div className="text-nano uppercase text-fg-muted">{label}</div>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-code border border-border p-3 text-micro leading-relaxed text-fg-code">{body}</pre>
      </div>
    ) : null;
  return (
    <QueryState query={query} empty={(d: TraceCallResponse) => !d.available}>
      {(d: TraceCallResponse) => {
        const c = d.call!;
        return (
          <div className="space-y-3">
            <Block label="System prompt" body={c.system} />
            <Block label="User prompt" body={c.user} />
            <Block label="Request" body={c.request} />
            <Block label="Response" body={c.response} />
            {c.error && <div className="text-xs text-danger-fg">error: {c.error}</div>}
          </div>
        );
      }}
    </QueryState>
  );
}

function CallTrace({ name }: { name: string }) {
  const query = useScenarioTrace(name);
  const [selected, setSelected] = useState<number | null>(null);
  return (
    <QueryState query={query} empty={(d: TraceResponse) => !d.available}>
      {(d: TraceResponse) => {
        const calls = d.calls ?? [];
        const maxLat = Math.max(...calls.map((c) => c.latency_ms), 1);
        return (
          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Call trace"
                subtitle={`${d.n_calls} calls · total $${(d.total_cost_usd ?? 0).toFixed(4)} · ${fmtLatency(d.total_latency_ms ?? 0)}`}
              />
              <CardBody className="flex flex-wrap gap-2">
                {Object.entries(d.by_service ?? {}).map(([svc, b]) => (
                  <span key={svc} className={cn("rounded px-2 py-1 text-xs", categoryClass(svc))}>
                    {svc}: {b.n_calls} call{b.n_calls === 1 ? "" : "s"}{b.n_errors > 0 ? ` · ${b.n_errors} err` : ""} · ${b.cost_usd.toFixed(4)} · {fmtLatency(b.latency_ms)}
                  </span>
                ))}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Calls" subtitle="latency waterfall — click a call for its request / response / prompt" />
              <div className="divide-y divide-border-subtle">
                {calls.map((c) => (
                  <div key={c.seq}>
                    <button
                      type="button"
                      onClick={() => setSelected(selected === c.seq ? null : c.seq)}
                      className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-surface-2"
                    >
                      <span className="w-5 shrink-0 text-right text-nano text-fg-muted">{c.seq}</span>
                      <span className={cn("w-16 shrink-0 rounded px-1.5 py-0.5 text-center text-nano", categoryClass(c.service))}>{c.service}</span>
                      <span className="w-52 shrink-0 truncate font-mono text-xs text-fg-secondary" title={c.purpose}>{c.purpose}</span>
                      <div className="relative h-3 flex-1 rounded bg-surface-0" title={fmtLatency(c.latency_ms)}>
                        <div className={cn("absolute top-0 h-3 rounded", c.ok ? "bg-fg-muted" : "bg-danger")} style={{ width: `${(c.latency_ms / maxLat) * 100}%` }} />
                      </div>
                      <span className="w-14 shrink-0 text-right tnum text-xs text-fg-secondary">{fmtLatency(c.latency_ms)}</span>
                      <span className="w-16 shrink-0 text-right tnum text-xs text-fg-secondary">{c.cost_usd > 0 ? `$${c.cost_usd.toFixed(4)}` : "—"}</span>
                    </button>
                    {selected === c.seq && (
                      <div className="bg-surface-2 px-4 py-3"><CallDetail name={name} seq={c.seq} /></div>
                    )}
                  </div>
                ))}
              </div>
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}

export function ScenarioDetail() {
  const { name = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const severity = params.get("severity") ?? undefined;
  const [tab, setTab] = useState<Tab>("derivation");
  const query = useScenarioDetail(name, severity);
  const ablation = useAblationScenario(name, query.data?.available ? query.data.severity : undefined);
  const layers = ablation.data?.available ? ablation.data.waterfall : undefined;

  return (
    <div className="space-y-4">
      <nav className="text-xs text-fg-secondary">
        <Link to="/scenarios" className="hover:underline">Scenarios</Link>
        <span className="mx-1 text-fg-muted">›</span>
        <span className="font-medium text-fg-secondary">{name}</span>
      </nav>

      <UnderTest />

      <QueryState query={query}>
        {(d) => {
          if (!d.available) {
            return <NotRunDetail d={d} />;
          }
          const sev = d.severity;
          const isLlm = d.source === "llm";
          const tabList: Tab[] = ["derivation"];
          if (layers) tabList.push("layers");
          if (d.attribution_available) tabList.push("valueadd");
          tabList.push("results", "plausibility");
          if (d.trace_available) tabList.push("trace");
          if (d.narrative_available) tabList.push("narrative");
          const activeTab: Tab = tabList.includes(tab) ? tab : "derivation";
          return (
            <div className="space-y-4">
              {/* narrative + governance */}
              <Card>
                <CardBody className="space-y-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h1 className="text-lg font-semibold text-fg">{name}</h1>
                        {isLlm && <span className="rounded bg-cat-violet-tint px-1.5 py-0.5 text-nano font-semibold text-cat-violet-fg">AI-generated</span>}
                      </div>
                      <p className="mt-0.5 max-w-2xl text-sm text-fg-secondary">{d.narrative}</p>
                      {d.trigger && <p className="mt-0.5 text-xs text-fg-muted">trigger: {d.trigger}</p>}
                    </div>
                    <div className="flex items-center gap-2">
                      {isLlm ? (
                        <span className="rounded border border-cat-violet-fg/20 bg-cat-violet-tint px-2 py-1 text-xs text-cat-violet-fg">severity: {sev}</span>
                      ) : (
                        <select
                          className="rounded border border-strong px-2 py-1 text-xs"
                          value={sev}
                          onChange={(e) => setParams({ severity: e.target.value })}
                        >
                          {(d.tiers ?? []).map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Zone status={plausibilityZone(d.plausibility?.status)} label={plausibilityLabel(d.plausibility?.status)} />
                    <Zone status={signoffZone(d.signoff?.status)} label={`sign-off ${d.signoff?.status ?? "pending"}`} />
                    <span className="text-xs text-fg-muted">horizon {d.horizon_days}d · {fmtMultiplier(d.severity_scale)} · {d.method}</span>
                    <ProvenanceBadge items={{ run_id: undefined, snapshot_id: d.snapshot_id, cov_id: d.covariance?.cov_id }} className="ml-auto" />
                  </div>
                </CardBody>
              </Card>

              {/* AI provenance (LLM-generated scenarios only) */}
              {isLlm && <AiProvenance d={d} />}

              {/* internal tabs */}
              <div className="flex gap-1 border-b border">
                {tabList.map((t) => (
                  <button key={t} type="button" onClick={() => setTab(t)} className={cn("-mb-px border-b-2 px-3 py-1.5 text-sm font-medium", activeTab === t ? "border-primary text-primary" : "border-transparent text-fg-secondary hover:text-fg")}>
                    {TAB_LABEL[t]}
                  </button>
                ))}
              </div>

              {activeTab === "derivation" && <ShockDerivation d={d} />}
              {activeTab === "layers" && layers && <AblationWaterfall w={layers} />}
              {activeTab === "valueadd" && <ServiceValueAdd name={name} />}
              {activeTab === "results" && <Results name={name} severity={sev} />}
              {activeTab === "plausibility" && <PlausibilityView d={d} />}
              {activeTab === "trace" && <CallTrace name={name} />}
              {activeTab === "narrative" && <Narrative name={name} severity={sev} />}
            </div>
          );
        }}
      </QueryState>
    </div>
  );
}
