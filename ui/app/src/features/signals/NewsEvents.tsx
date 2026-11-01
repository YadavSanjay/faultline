import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useNewsEvents, useNewsCalibration, type NewsEvent, type NewsLeg } from "@/lib/api";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { cn } from "@/lib/cn";
import { num } from "./format";

// P6/A5: News & Events — the SEC 8-K item-code event stream + GDELT-tone-informed calibration.
// Shows what the option-and-fundamentals-blind covariance model can't see: a live 5.02 officer
// departure firing keyman_nvda, and how recent filings scale each name's idiosyncratic shock.

const KEYMAN = "5.02";

function multTone(m: number): string {
  if (m >= 1.25) return "text-danger-fg";
  if (m > 1.0) return "text-warning-fg";
  if (m < 1.0) return "text-success-fg";
  return "text-fg-secondary";
}

function ItemChip({ code, label }: { code: string; label: string }) {
  const keyman = code === KEYMAN;
  return (
    <span
      title={label}
      className={cn(
        "inline-block rounded px-1.5 py-0.5 text-nano font-semibold",
        keyman ? "bg-danger-tint text-danger-fg" : "bg-surface-0 text-fg-secondary",
      )}
    >
      {code}
    </span>
  );
}

function CalibrationPanel({ asOf }: { asOf?: string }) {
  const query = useNewsCalibration(asOf);
  return (
    <QueryState query={query}>
      {(c) => {
        const triggers = c.live_triggers[KEYMAN] ?? [];
        return (
          <Card>
            <CardHeader
              title="Scenario calibration"
              subtitle={`How recent filings scale each name's idiosyncratic shock — point-in-time to ${c.as_of}`}
              right={
                <span className={cn("rounded px-2 py-0.5 text-xs font-medium",
                  c.applied ? "bg-success-tint text-success-fg" : "bg-surface-0 text-fg-muted")}>
                  {c.applied ? "applied" : `held — ${c.gate.reason}`}
                </span>
              }
            />
            <CardBody className="space-y-3">
              {triggers.length > 0 && (
                <div className="rounded-md border border-danger bg-danger-tint px-3 py-2 text-xs text-danger-fg">
                  <span className="font-semibold">Live trigger — 5.02 {c.live_trigger_labels[KEYMAN]}:</span>{" "}
                  {triggers.join(", ")}. A recent officer/director departure makes the{" "}
                  <span className="font-mono">keyman_nvda</span> scenario live, not hypothetical.
                </div>
              )}
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-3 py-2 font-medium">Name</th>
                      <th className="px-3 py-2 text-right font-medium">Multiplier</th>
                      <th className="px-3 py-2 text-right font-medium">Event score</th>
                      <th className="px-3 py-2 text-right font-medium">Tone z</th>
                      <th className="px-3 py-2 text-right font-medium">Events</th>
                      <th className="px-3 py-2 font-medium">Items</th>
                      <th className="px-3 py-2 font-medium">Next earnings</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {Object.values(c.legs)
                      .sort((a, b) => b.multiplier - a.multiplier)
                      .map((leg: NewsLeg) => (
                        <tr key={leg.ticker} className="hover:bg-surface-2">
                          <td className="px-3 py-2">
                            <Link to={`/signals/${leg.ticker}`} className="font-medium text-fg hover:underline">
                              {leg.ticker}
                            </Link>
                          </td>
                          <td className={cn("px-3 py-2 text-right tnum font-medium", multTone(leg.multiplier))}>
                            {num(leg.multiplier, 3)}×
                          </td>
                          <td className="px-3 py-2 text-right tnum text-fg-secondary">{num(leg.event_score, 2)}</td>
                          <td className="px-3 py-2 text-right tnum text-fg-secondary">{leg.tone_z == null ? "—" : num(leg.tone_z, 2)}</td>
                          <td className="px-3 py-2 text-right tnum text-fg-secondary">{leg.n_events}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              {leg.item_codes.slice(0, 6).map((code) => (
                                <ItemChip key={code} code={code} label={code} />
                              ))}
                            </div>
                          </td>
                          <td className="px-3 py-2 tnum text-xs text-fg-secondary">
                            {c.earnings[leg.ticker]?.[0] ?? "—"}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              <p className="text-micro leading-relaxed text-fg-muted">
                The multiplier is <span className="font-mono">clamp(1 + β·event_score(age-decayed) − β·tone_z, 0.8, 1.5)</span>{" "}
                on the ARag idiosyncratic leg — mode-(ii), so it never enters the covariance matrix. News modulates a
                governed baseline; it can never invent one.
              </p>
            </CardBody>
          </Card>
        );
      }}
    </QueryState>
  );
}

function EventRow({ e, labels }: { e: NewsEvent; labels: Record<string, string> }) {
  return (
    <tr className="hover:bg-surface-2">
      <td className="px-3 py-2 tnum text-xs text-fg-secondary">{e.filing_date}</td>
      <td className="px-3 py-2">
        <Link to={`/signals/${e.ticker}`} className="font-medium text-fg hover:underline">{e.ticker}</Link>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {e.items.map((it) => <ItemChip key={it.code} code={it.code} label={it.label} />)}
        </div>
      </td>
      <td className="px-3 py-2 text-xs text-fg-secondary" style={{ maxWidth: "32ch" }}>
        {e.items.map((it) => labels[it.code] ?? it.label).join("; ")}
      </td>
      <td className="px-3 py-2 text-right tnum text-fg-secondary">{num(e.weight, 2)}</td>
      <td className="px-3 py-2">
        <a href={e.doc_url} target="_blank" rel="noreferrer"
           className="font-mono text-micro text-primary hover:underline">{e.accession}</a>
      </td>
    </tr>
  );
}

export function NewsEvents() {
  const [ticker, setTicker] = useState<string | null>(null);
  const query = useNewsEvents(ticker ?? undefined);

  const chips = useMemo(() => query.data?.tickers ?? [], [query.data]);

  return (
    <div className="space-y-4">
      <div className="rounded-md border border bg-surface-2 px-4 py-2.5 text-xs leading-relaxed text-fg-secondary">
        <span className="font-medium text-fg">SEC Form 8-K event stream.</span>{" "}
        An 8-K is filed only when something material happens, and the filer tags which material thing from a fixed
        numbered taxonomy under legal liability — so the classification is done and citable by accession number, no
        LLM. This is the event complement to ARag's slow-moving 10-K/Q disclosure, entering scenarios as a clamped
        multiplier (below), with a <span className="font-medium text-danger-fg">5.02</span> departure firing the
        key-man scenario live.
      </div>

      <CalibrationPanel />

      <Card>
        <CardHeader title="8-K event feed" subtitle="Allowlisted material events, newest first" />
        <CardBody>
          <div className="mb-3 flex flex-wrap gap-1">
            <button type="button" onClick={() => setTicker(null)}
              className={cn("rounded px-2.5 py-1 text-xs font-medium",
                ticker === null ? "bg-primary text-primary-fg" : "bg-surface-1 text-fg-secondary hover:bg-surface-0")}>
              all names
            </button>
            {chips.map((t) => (
              <button key={t} type="button" onClick={() => setTicker(t)}
                className={cn("rounded px-2.5 py-1 text-xs font-medium",
                  ticker === t ? "bg-primary text-primary-fg" : "bg-surface-1 text-fg-secondary hover:bg-surface-0")}>
                {t}
              </button>
            ))}
          </div>
          <QueryState query={query} empty={(d) => d.count === 0}>
            {(data) => (
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface-2 text-left text-xs text-fg-secondary">
                    <tr>
                      <th className="px-3 py-2 font-medium">Filed</th>
                      <th className="px-3 py-2 font-medium">Name</th>
                      <th className="px-3 py-2 font-medium">Items</th>
                      <th className="px-3 py-2 font-medium">What happened</th>
                      <th className="px-3 py-2 text-right font-medium">Weight</th>
                      <th className="px-3 py-2 font-medium">Filing</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-subtle">
                    {data.events.map((e) => (
                      <EventRow key={`${e.ticker}-${e.accession}`} e={e} labels={data.item_labels} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </CardBody>
      </Card>
    </div>
  );
}
