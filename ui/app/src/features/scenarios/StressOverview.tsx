import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  useBook,
  useReverseStress,
  useScenarioAttribution,
  useScenarioCatalog,
  useScenarioNarrative,
  useScenarioResults,
  useSignalHealth,
  type ScenarioCatalogResponse,
} from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { PageHeader } from "@/components/shared/PageHeader";
import { QueryState } from "@/components/shared/QueryState";
import { Markdown } from "@/components/shared/Markdown";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { exactUsd, humanizeUsd } from "@/lib/format";
import { cn } from "@/lib/cn";
import { plausibilityCounts, plausibilityLabel, plausibilityZone, signoffZone } from "./format";
import { UnderTest } from "./UnderTest";

// Stress Overview — the "where does this book break?" home. Four headline tiles (worst
// loss, ES under that shock, the reverse-stress break point, evidence & sign-off), the
// trades that carry the worst loss, then every scenario ranked by its worst tier with a
// collapsible narrative. Narratives are not new data: the authored story comes from the
// scenario config (catalog cell), the historical analog and "what the evidence changed"
// from the dual-RAG attribution, and the full write-up of an LLM scenario from its
// narrative file. Each is fetched only when its row is opened.

const TIERS = ["mild", "adverse", "severe"] as const;

interface TierCell {
  severity: string;
  pnl: number | null;
  ran: boolean;
  plausibility: string | null;
  signoff: string;
}

interface ScenarioRow {
  name: string;
  source: "named" | "llm";
  narrative: string;
  tiers: TierCell[];
  worst: { severity: string; pnl: number } | null;
  worldView?: string; // LLM scenarios: the MacroRAG world view the run was grounded on
}

function buildRows(cat: ScenarioCatalogResponse): ScenarioRow[] {
  const rows: ScenarioRow[] = cat.scenarios.map((name) => {
    const cells = cat.cells.filter((c) => c.scenario === name);
    const tiers = TIERS.map((sev): TierCell => {
      const c = cells.find((x) => x.severity === sev);
      return {
        severity: sev,
        pnl: c?.ran ? c.total_pnl : null,
        ran: !!c?.ran,
        plausibility: c?.plausibility_status ?? null,
        signoff: c?.signoff ?? "pending",
      };
    });
    return { name, source: "named", narrative: cells.find((c) => c.narrative)?.narrative ?? "", tiers, worst: null };
  });
  for (const l of cat.llm_scenarios ?? []) {
    rows.push({
      name: l.scenario,
      source: "llm",
      narrative: l.trigger,
      tiers: [{ severity: l.severity, pnl: l.total_pnl, ran: l.total_pnl != null, plausibility: l.plausibility_status, signoff: "pending" }],
      worst: null,
      worldView: l.world_view_id,
    });
  }
  for (const r of rows) {
    for (const t of r.tiers) {
      if (t.pnl != null && (r.worst == null || t.pnl < r.worst.pnl)) r.worst = { severity: t.severity, pnl: t.pnl };
    }
  }
  // worst loss first; scenarios that never ran sink to the bottom
  // ranked by the severe tier (an LLM scenario by its single run); never-run scenarios sink
  return rows.sort((a, b) => (headlinePnl(a) ?? Infinity) - (headlinePnl(b) ?? Infinity));
}

// A few-word label for a collapsed row: the authored narrative up to its first colon
// ("AI-to-broad-market rotation: the 10-24 Jul 2024 replay ..." -> "AI-to-broad-market rotation").
// The full narrative shows only when the row is opened.
function shortLabel(narrative: string): string {
  const head = narrative.split(":")[0].trim();
  return head.length > 0 && head.length <= 60 ? head : "";
}

// The P&L a row leads with: severe for a named scenario, the single run for an LLM one.
function headlinePnl(r: ScenarioRow): number | null {
  return r.source === "llm" ? (r.tiers[0]?.pnl ?? null) : (r.tiers.find((t) => t.severity === "severe")?.pnl ?? null);
}

function scenarioHref(name: string, severity?: string): string {
  return `/scenarios/${encodeURIComponent(name)}${severity ? `?severity=${severity}` : ""}`;
}

function pnlClass(pnl: number | null): string {
  if (pnl == null) return "text-fg-muted";
  return pnl < 0 ? "text-danger-fg" : "text-success-fg";
}

// --- stress-loss limits (configs/scenariogen/limits.json) ----------------- //

interface Limits {
  limit: number | null; // hard loss limit, positive USD
  warning: number | null; // early-warning trigger, positive USD
}

type LimitLevel = "breach" | "warning" | null;

function limitLevel(pnl: number | null, limits: Limits): LimitLevel {
  if (pnl == null || pnl >= 0) return null;
  const loss = -pnl;
  if (limits.limit != null && loss >= limits.limit) return "breach";
  if (limits.warning != null && loss >= limits.warning) return "warning";
  return null;
}

// glyph + hover text so the state never relies on colour alone
function LimitMark({ pnl, limits }: { pnl: number | null; limits: Limits }) {
  const level = limitLevel(pnl, limits);
  if (level === "breach") {
    return <span className="ml-1 text-danger-fg" title={`at or over the ${humanizeUsd(limits.limit)} loss limit`}>✗</span>;
  }
  if (level === "warning") {
    return <span className="ml-1 text-warning-fg" title={`at or over the ${humanizeUsd(limits.warning)} early-warning trigger`}>⚠</span>;
  }
  return null;
}

// --- headline tiles -------------------------------------------------------- //

function Tile({ label, children, foot }: { label: string; children: React.ReactNode; foot?: React.ReactNode }) {
  return (
    <Card>
      <CardBody className="space-y-1">
        <div className="text-xs uppercase tracking-wide text-fg-muted">{label}</div>
        {children}
        {foot && <div className="text-xs text-fg-secondary">{foot}</div>}
      </CardBody>
    </Card>
  );
}

function WorstLossTile({ row, limits }: { row: ScenarioRow | undefined; limits: Limits }) {
  if (!row?.worst) return <Tile label="Worst loss">—</Tile>;
  const level = limitLevel(row.worst.pnl, limits);
  return (
    <Tile
      label="Worst loss"
      foot={
        <span className="space-y-0.5">
          {limits.limit != null && (
            <span className={cn("block", level === "breach" ? "text-danger-fg" : level === "warning" ? "text-warning-fg" : "")}>
              {level === "breach" ? "✗ over" : level === "warning" ? "⚠ past the warning, under" : "✓ within"} the{" "}
              {humanizeUsd(limits.limit)} limit
            </span>
          )}
          <Link to={scenarioHref(row.name, row.worst.severity)} className="block hover:underline">
            {row.name} · {row.worst.severity} ›
          </Link>
        </span>
      }
    >
      <div className={cn("tnum text-2xl font-semibold", pnlClass(row.worst.pnl))} title={exactUsd(row.worst.pnl)}>
        {humanizeUsd(row.worst.pnl)}
      </div>
    </Tile>
  );
}

function EsTile({ row }: { row: ScenarioRow | undefined }) {
  const res = useScenarioResults(row?.name ?? "", row?.worst?.severity);
  const cap = res.data?.available ? res.data.capital : null;
  if (!cap) return <Tile label="Expected shortfall under that shock">—</Tile>;
  return (
    <Tile
      label="Expected shortfall under that shock"
      foot={`ES ${Math.round(cap.level * 1000) / 10}% · ${cap.lookback_days}d lookback · base → shocked`}
    >
      <div className="tnum text-2xl font-semibold text-fg">
        <span title={exactUsd(cap.es_base)}>{humanizeUsd(cap.es_base)}</span>
        <span className="mx-1 text-fg-muted">→</span>
        <span title={exactUsd(cap.es_shocked)}>{humanizeUsd(cap.es_shocked)}</span>
      </div>
    </Tile>
  );
}

function BreakPointTile({ scenarioDate }: { scenarioDate: string | null }) {
  const rev = useReverseStress();
  const d = rev.data;
  if (!d?.available) return <Tile label="Breaks at" foot="no reverse-stress run stored">—</Tile>;
  const reachable = d.results.filter((r) => r.reachable && r.severity_scale != null);
  const fastest = [...reachable].sort((a, b) => (a.severity_scale ?? 0) - (b.severity_scale ?? 0))[0];
  const stale = scenarioDate != null && d.valuation_date !== scenarioDate;
  return (
    <Tile
      label={`Breaks at — ${humanizeUsd(d.target_loss)} loss`}
      foot={
        <span className={cn(stale && "text-warning-fg")}>
          {stale
            ? `⚠ reverse stress last ran ${d.valuation_date}; scenarios ran ${scenarioDate}`
            : `${d.n_reachable}/${d.n_scenarios} scenarios can reach it`}
        </span>
      }
    >
      {fastest ? (
        <div>
          <div className="tnum text-2xl font-semibold text-fg">{fastest.severity_scale?.toFixed(2)}× severity</div>
          <Link to="/scenarios/reverse" className="text-xs text-fg-secondary hover:underline">
            of {fastest.scenario} — the shortest path ›
          </Link>
        </div>
      ) : (
        <div className="text-sm text-fg-secondary">no scenario reaches it</div>
      )}
    </Tile>
  );
}

function healthZone(overall: string | null | undefined): ZoneStatus {
  if (overall === "ok") return "green";
  if (overall === "degraded") return "amber";
  if (overall === "down") return "red";
  return "unknown";
}

function EvidenceTile({ rows }: { rows: ScenarioRow[] }) {
  const health = useSignalHealth();
  const ran = rows.flatMap((r) => (r.source === "named" ? r.tiers.filter((t) => t.ran) : []));
  const approved = ran.filter((t) => t.signoff === "approved").length;
  const pc = plausibilityCounts(ran.map((t) => t.plausibility));
  const plausibleZone: ZoneStatus = pc.failed > 0 ? "red" : pc.plausible === pc.total && pc.total > 0 ? "green" : "amber";
  const notes = pc.withNotes > 0 ? ` · ${pc.withNotes} with notes` : "";
  const h = health.data?.available ? health.data : null;
  return (
    <Tile label="Can we trust it" foot={<Link to="/scenarios/signal-health" className="hover:underline">signal health ›</Link>}>
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        <Zone status={healthZone(h?.overall)} label={`signals ${h?.overall ?? "unknown"}`} />
        <Zone status={plausibleZone} label={`${pc.plausible}/${pc.total} plausible${notes}`} />
        <Zone status={approved === ran.length && ran.length > 0 ? "green" : "unknown"} label={`${approved}/${ran.length} signed off`} />
      </div>
    </Tile>
  );
}

// --- where it hurts -------------------------------------------------------- //

function WhereItHurts({ row, valuationDate }: { row: ScenarioRow | undefined; valuationDate: string | null }) {
  const res = useScenarioResults(row?.name ?? "", row?.worst?.severity);
  const trades = useMemo(
    () => [...(res.data?.trades ?? [])].sort((a, b) => a.pnl - b.pnl).slice(0, 6),
    [res.data],
  );
  // position descriptions come from the book priced on the scenarios' date
  const book = useBook(valuationDate ?? undefined);
  const describe = useMemo(
    () => new Map((book.data?.trades ?? []).map((t) => [t.trade_id, t.description])),
    [book.data],
  );
  if (!row?.worst) return null;
  const maxLoss = Math.max(1, ...trades.map((t) => Math.abs(Math.min(0, t.pnl))));
  // fixed columns packed left, so the numbers sit next to the position they describe
  const cols = "grid grid-cols-[minmax(0,24rem)_6rem_6.5rem_7rem_4.5rem_6rem] items-center gap-4";
  return (
    <Card>
      <CardHeader
        title="Where it hurts"
        subtitle={`Positions carrying the worst loss — ${row.name} · ${row.worst.severity}`}
        right={<Link to={scenarioHref(row.name, row.worst.severity)} className="text-xs text-fg-secondary hover:underline">all trades ›</Link>}
      />
      <CardBody className="space-y-0.5">
        <div className={cn(cols, "px-1 pb-1 text-nano uppercase tracking-wider text-fg-muted")}>
          <span>position</span>
          <span>asset class</span>
          <span className="text-right">PV</span>
          <span className="text-right">stressed P&amp;L</span>
          <span className="text-right">% of PV</span>
          <span />
        </div>
        {trades.map((t) => {
          const loss = Math.abs(Math.min(0, t.pnl));
          const pct = t.base_pv !== 0 ? (t.pnl / Math.abs(t.base_pv)) * 100 : null;
          return (
            <Link
              key={t.trade_id}
              to={`/pricing/trade/${t.trade_id}`}
              className={cn(cols, "rounded px-1 py-1 text-body-sm hover:bg-surface-0")}
              title={`${t.trade_id} — ${describe.get(t.trade_id) ?? ""}\nPV ${exactUsd(t.base_pv)} → ${exactUsd(t.scenario_pv)}\nP&L ${exactUsd(t.pnl)}`}
            >
              <span className="min-w-0">
                <span className="block truncate text-fg">{describe.get(t.trade_id) || t.trade_id}</span>
                <span className="block font-mono text-micro text-fg-muted">{t.trade_id}</span>
              </span>
              <span className="text-fg-secondary">{t.asset_class}</span>
              <span className="tnum text-right text-fg-secondary">{humanizeUsd(t.base_pv)}</span>
              <span className={cn("tnum text-right font-semibold", pnlClass(t.pnl))}>{humanizeUsd(t.pnl)}</span>
              <span className={cn("tnum text-right", pnlClass(t.pnl))}>{pct == null ? "—" : `${pct.toFixed(0)}%`}</span>
              <span className="h-1.5 rounded-r bg-surface-2">
                <span className="block h-1.5 rounded-r bg-primary" style={{ width: `${(loss / maxLoss) * 100}%` }} />
              </span>
            </Link>
          );
        })}
      </CardBody>
    </Card>
  );
}

// --- scenario ladder with collapsible narratives --------------------------- //

function Paragraphs({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {text.split(/\n\s*\n/).map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}

function NarrativeSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-micro font-bold uppercase tracking-wider text-fg-muted">{title}</div>
      <div className="text-body-sm leading-relaxed text-fg-secondary">{children}</div>
    </div>
  );
}

function NamedNarrative({ row }: { row: ScenarioRow }) {
  const attr = useScenarioAttribution(row.name);
  const a = attr.data;
  const analog = a?.attribution?.macrorag?.summary;
  const changed = a?.narrative?.text;
  return (
    <div className="space-y-3">
      <NarrativeSection title="The story">{row.narrative || "No authored narrative."}</NarrativeSection>
      {attr.isLoading && <div className="text-xs text-fg-muted">loading evidence…</div>}
      {analog && <NarrativeSection title="Closest historical analog">{analog}</NarrativeSection>}
      {changed && (
        <NarrativeSection title={`What the evidence changed${a?.severity ? ` (${a.severity} run)` : ""}`}>
          <Paragraphs text={changed} />
        </NarrativeSection>
      )}
      {a && !a.available && (
        <div className="text-xs text-fg-muted">No evidence narrative for this scenario{a.reason ? ` — ${a.reason}` : ""}.</div>
      )}
    </div>
  );
}

function LlmNarrative({ row }: { row: ScenarioRow }) {
  const sev = row.tiers[0]?.severity;
  const nar = useScenarioNarrative(row.name, sev);
  return (
    <div className="space-y-3">
      <NarrativeSection title="Trigger">{row.narrative || "—"}</NarrativeSection>
      {row.worldView && (
        <NarrativeSection title="Grounded on world view">
          <span className="font-mono text-xs">{row.worldView}</span>
        </NarrativeSection>
      )}
      {nar.isLoading && <div className="text-xs text-fg-muted">loading narrative…</div>}
      {nar.data?.available && nar.data.markdown ? (
        <NarrativeSection title="AI-generated narrative">
          <Markdown source={nar.data.markdown} />
        </NarrativeSection>
      ) : (
        nar.data && <div className="text-xs text-fg-muted">No narrative file{nar.data.reason ? ` — ${nar.data.reason}` : ""}.</div>
      )}
    </div>
  );
}

function ScenarioLadderRow({ row, maxLoss, limits, open, onToggle }: {
  row: ScenarioRow;
  maxLoss: number;
  limits: Limits;
  open: boolean;
  onToggle: () => void;
}) {
  const headline = headlinePnl(row);
  const loss = headline != null ? Math.abs(Math.min(0, headline)) : 0;
  const tierPnl = (sev: string) => row.tiers.find((x) => x.severity === sev)?.pnl ?? null;
  return (
    <div className="border-b border-subtle last:border-b-0">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="grid w-full grid-cols-[1rem_minmax(0,20rem)_8.5rem_7.5rem_7.5rem] items-center gap-6 px-4 py-2.5 text-left hover:bg-surface-0"
      >
        <span className="text-nano text-fg-muted">{open ? "▾" : "▸"}</span>
        <span className="min-w-0">
          <span className="block truncate text-body-sm font-semibold text-fg">{row.name}</span>
          <span className="block truncate text-micro text-fg-secondary">
            {row.source === "llm" ? "AI-generated" : shortLabel(row.narrative)}
          </span>
        </span>
        {/* severe leads: largest, boldest, carries the bar. An LLM scenario's single run sits here. */}
        <span className="text-right" title={headline != null ? exactUsd(headline) : "not run"}>
          <span className="block text-nano font-bold uppercase tracking-wider text-fg-secondary">
            {row.source === "llm" ? "single run" : "severe"}
          </span>
          <span className={cn("tnum block text-base font-bold", pnlClass(headline))}>
            {headline != null ? humanizeUsd(headline) : "—"}
            <LimitMark pnl={headline} limits={limits} />
          </span>
          <span className="mt-0.5 ml-auto block h-1 w-full rounded-r bg-surface-2">
            <span className="ml-auto block h-1 rounded-l bg-primary" style={{ width: `${(loss / maxLoss) * 100}%` }} />
          </span>
        </span>
        {(["adverse", "mild"] as const).map((sev) => {
          const pnl = row.source === "llm" ? null : tierPnl(sev);
          return (
            <span key={sev} className="text-right" title={pnl != null ? `${sev}: ${exactUsd(pnl)}` : `${sev}: not run`}>
              <span className="block text-nano uppercase tracking-wider text-fg-muted">{sev}</span>
              <span className={cn("tnum text-body-sm", sev === "mild" && "opacity-75", pnlClass(pnl))}>
                {pnl != null ? humanizeUsd(pnl) : "—"}
              </span>
              <LimitMark pnl={pnl} limits={limits} />
            </span>
          );
        })}
      </button>
      {open && (
        <div className="space-y-3 bg-surface-0 px-11 py-3">
          {row.source === "llm" ? <LlmNarrative row={row} /> : <NamedNarrative row={row} />}
          <div className="flex flex-wrap items-center gap-1.5">
            {row.tiers.filter((t) => t.ran).map((t) => (
              <span key={t.severity} className="flex items-center gap-1">
                <span className="text-micro text-fg-muted">{t.severity}</span>
                <Zone status={plausibilityZone(t.plausibility)} label={plausibilityLabel(t.plausibility)} />
                {row.source === "named" && <Zone status={signoffZone(t.signoff)} label={t.signoff} />}
              </span>
            ))}
            <Link to={scenarioHref(row.name, row.worst?.severity)} className="ml-auto text-xs text-fg-secondary hover:underline">
              shocks, results &amp; trace ›
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

// Header line: the thresholds and how many tiers cross each (named scenarios and LLM runs alike).
function limitSummary(rows: ScenarioRow[], limits: Limits): string {
  const base = "Severe leads; adverse and mild follow. Open a row for its narrative and evidence.";
  if (limits.limit == null) return base;
  const levels = rows.flatMap((r) => r.tiers.map((t) => limitLevel(t.pnl, limits)));
  const breaches = levels.filter((l) => l === "breach").length;
  const warnings = levels.filter((l) => l === "warning").length;
  const warn = limits.warning != null ? ` · ⚠ warning ${humanizeUsd(limits.warning)}: ${warnings} more` : "";
  return `✗ loss limit ${humanizeUsd(limits.limit)}: ${breaches} of ${levels.length} tiers over${warn}. ${base}`;
}

function ScenarioLadder({ rows, limits }: { rows: ScenarioRow[]; limits: Limits }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const allOpen = rows.length > 0 && open.size === rows.length;
  const maxLoss = Math.max(1, ...rows.map((r) => Math.abs(Math.min(0, headlinePnl(r) ?? 0))));
  const toggle = (name: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  return (
    <Card>
      <CardHeader
        title="Every scenario, by severe loss"
        subtitle={limitSummary(rows, limits)}
        right={
          <button
            type="button"
            onClick={() => setOpen(allOpen ? new Set() : new Set(rows.map((r) => r.name)))}
            className="text-xs text-fg-secondary hover:text-fg"
          >
            {allOpen ? "collapse all" : "expand all"}
          </button>
        }
      />
      <div>
        {rows.map((r) => (
          <ScenarioLadderRow key={r.name} row={r} maxLoss={maxLoss} limits={limits} open={open.has(r.name)} onToggle={() => toggle(r.name)} />
        ))}
      </div>
    </Card>
  );
}

// --- page ------------------------------------------------------------------ //

export function StressOverview() {
  const query = useScenarioCatalog();
  return (
    <div>
      <PageHeader
        title="Where it breaks"
        subtitle="The worst losses the book takes under stress, how close it is to its break point, and the story behind each scenario."
      />
      <UnderTest />
      <QueryState query={query}>
        {(cat) => <OverviewBody cat={cat} />}
      </QueryState>
    </div>
  );
}

function OverviewBody({ cat }: { cat: ScenarioCatalogResponse }) {
  const rows = useMemo(() => buildRows(cat), [cat]);
  // headline tiles use the named set only; AI-generated runs are exploratory and listed below
  const worst = rows
    .filter((r) => r.source === "named" && r.worst)
    .sort((a, b) => (a.worst?.pnl ?? 0) - (b.worst?.pnl ?? 0))[0];
  const limits: Limits = { limit: cat.limits?.loss_limit ?? null, warning: cat.limits?.loss_warning ?? null };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <WorstLossTile row={worst} limits={limits} />
        <EsTile row={worst} />
        <BreakPointTile scenarioDate={cat.valuation_date} />
        <EvidenceTile rows={rows} />
      </div>
      <WhereItHurts row={worst} valuationDate={cat.valuation_date} />
      <ScenarioLadder rows={rows} limits={limits} />
    </div>
  );
}
