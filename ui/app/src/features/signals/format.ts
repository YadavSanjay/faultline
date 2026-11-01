// Small formatters shared across the Market Signals views.

export const pct = (v: number | null | undefined, d = 1): string =>
  v == null || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(d)}%`;

export const num = (v: number | null | undefined, d = 2): string =>
  v == null || Number.isNaN(v) ? "—" : v.toFixed(d);

export const bp = (v: number | null | undefined, d = 0): string =>
  v == null || Number.isNaN(v) ? "—" : `${v.toFixed(d)}bp`;

// Tone for a distance-to-default: low DD = closer to default = warmer.
export function ddTone(dd: number | null | undefined): string {
  if (dd == null) return "text-fg-muted";
  if (dd < 4) return "text-danger-fg";
  if (dd < 8) return "text-warning-fg";
  return "text-success-fg";
}
