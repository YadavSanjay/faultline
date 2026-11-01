import { cn } from "@/lib/cn";

// Status traffic-light pill (RISK/SCENARIO/GOVERNANCE docs). Always pairs color with a
// glyph + label so it never relies on color alone.

export type ZoneStatus = "green" | "amber" | "red" | "unknown" | "not_run";

// Color + glyph + label — the glyph is the non-color signal, so the badge never
// relies on color alone (a redundant color dot was removed for a calmer read).
const STYLES: Record<ZoneStatus, { text: string; bg: string; glyph: string }> = {
  green: { text: "text-success-fg", bg: "bg-success-tint", glyph: "✓" },
  amber: { text: "text-warning-fg", bg: "bg-warning-tint", glyph: "⚠" },
  red: { text: "text-danger-fg", bg: "bg-danger-tint", glyph: "✗" },
  unknown: { text: "text-fg-secondary", bg: "bg-surface-2", glyph: "·" },
  not_run: { text: "text-fg-muted", bg: "bg-surface-2", glyph: "○" },
};

export function Zone({ status, label, className }: {
  status: ZoneStatus;
  label?: string;
  className?: string;
}) {
  const s = STYLES[status] ?? STYLES.unknown;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        s.bg,
        s.text,
        className,
      )}
    >
      <span aria-hidden>{s.glyph}</span>
      {label ?? status}
    </span>
  );
}
