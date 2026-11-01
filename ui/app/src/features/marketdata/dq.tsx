import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import type { ConsumedBy, DqVerdict } from "@/lib/api";

// DQ verdict → Zone (adds "ungated" for feeds not declared in the gate).
const MAP: Record<DqVerdict, { status: ZoneStatus; label: string }> = {
  ok: { status: "green", label: "ok" },
  warn: { status: "amber", label: "warn" },
  block: { status: "red", label: "blocked" },
  ungated: { status: "unknown", label: "ungated" },
};

export function DqVerdictBadge({ verdict }: { verdict: DqVerdict }) {
  const m = MAP[verdict] ?? MAP.ungated;
  return <Zone status={m.status} label={m.label} />;
}

export function staleTone(days: number | null | undefined, max = 7): string {
  if (days == null) return "text-fg-muted";
  return days > max ? "text-warning-fg" : "text-fg-secondary";
}

// consumed_by → badge. "none" is neutral (reference/redundant/roadmap), NOT an error.
const CONSUMED: Record<ConsumedBy, { cls: string; label: string; title: string }> = {
  both: { cls: "bg-cat-indigo-tint text-cat-indigo-fg", label: "PV + Risk", title: "Priced with (PV) and a VaR/risk factor" },
  pv: { cls: "bg-success-tint text-success-fg", label: "PV", title: "Read at valuation time (pricing input)" },
  risk: { cls: "bg-info-tint text-info-fg", label: "Risk", title: "A VaR / risk factor (not a PV input)" },
  none: { cls: "bg-surface-0 text-fg-secondary", label: "not consumed", title: "Downloaded but not read by pricing/risk (reference, redundant, or roadmap)" },
};

export function ConsumedByBadge({ consumedBy }: { consumedBy: ConsumedBy }) {
  const m = CONSUMED[consumedBy] ?? CONSUMED.none;
  return (
    <span title={m.title} className={`inline-block rounded px-1.5 py-0.5 text-micro font-medium ${m.cls}`}>
      {m.label}
    </span>
  );
}
