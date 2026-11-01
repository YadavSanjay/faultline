import { Zone, type ZoneStatus } from "./Zone";

// Data-quality badge (MARKETDATA/PRICING). Maps DQ severity to the shared Zone palette.
// fresh/ok → green, warn/stale → amber, block → red.

export type DqSeverity = "ok" | "warn" | "block" | "fresh" | "stale" | "blocked";

const MAP: Record<DqSeverity, { status: ZoneStatus; label: string }> = {
  ok: { status: "green", label: "ok" },
  fresh: { status: "green", label: "fresh" },
  warn: { status: "amber", label: "warn" },
  stale: { status: "amber", label: "stale" },
  block: { status: "red", label: "blocked" },
  blocked: { status: "red", label: "blocked" },
};

export function DqBadge({ severity }: { severity: DqSeverity }) {
  const m = MAP[severity] ?? { status: "unknown" as ZoneStatus, label: severity };
  return <Zone status={m.status} label={m.label} />;
}
