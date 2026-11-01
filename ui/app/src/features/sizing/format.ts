// Sizing-specific display helpers + per-profile visual identity.

export const PROFILE_ORDER = ["balanced", "aggressive", "risky"] as const;
export type ProfileKey = (typeof PROFILE_ORDER)[number];

export interface ProfileTheme {
  label: string;
  blurb: string;
  accent: string; // text
  bar: string; // bg for bars / accents
  ring: string; // top border
  soft: string; // soft background
}

export const PROFILE_THEME: Record<string, ProfileTheme> = {
  balanced: {
    label: "Balanced",
    blurb: "Diversified · full hedges · ~1% daily VaR",
    accent: "text-blue-700",
    bar: "bg-blue-500",
    ring: "border-t-blue-500",
    soft: "bg-blue-50",
  },
  aggressive: {
    label: "Aggressive Growth",
    blurb: "Return-tilted · half hedges · ~2.5% daily VaR",
    accent: "text-teal-700",
    bar: "bg-teal-500",
    ring: "border-t-teal-500",
    soft: "bg-teal-50",
  },
  risky: {
    label: "Highly Leveraged / Risky",
    blurb: "Concentrated · minimal hedges · ~5% daily VaR",
    accent: "text-rose-700",
    bar: "bg-rose-500",
    ring: "border-t-rose-500",
    soft: "bg-rose-50",
  },
};

export function themeFor(profile: string): ProfileTheme {
  return PROFILE_THEME[profile] ?? PROFILE_THEME.balanced;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`;
}

// Direction tag from the sizing size + hedge flag.
export function directionTag(size: { is_hedge: boolean; target_notional: number; current_notional: number }): {
  label: string;
  cls: string;
} {
  if (size.is_hedge) return { label: "HEDGE", cls: "bg-amber-100 text-amber-700" };
  const short = size.current_notional < 0 || size.target_notional < 0;
  return short
    ? { label: "SHORT", cls: "bg-rose-100 text-rose-700" }
    : { label: "LONG", cls: "bg-emerald-100 text-emerald-700" };
}

// Multiplier tone vs current size.
export function multiplierTone(m: number, isHedge: boolean): string {
  if (isHedge) return "text-amber-600";
  if (m >= 1.15) return "text-teal-700";
  if (m <= 0.85) return "text-slate-400";
  return "text-slate-600";
}

const METHOD_LABEL: Record<string, string> = {
  equity_erp: "equity β·ERP",
  bond_carry: "bond carry",
  swap_carry: "swap carry",
  option_theta: "θ decay",
  option_delta_theta: "δ view + θ",
  cds_carry: "CDS carry",
  fx_carry: "FX carry",
  xccy_carry: "xccy carry",
  none: "—",
};

export function methodLabel(method: string | undefined): string {
  return METHOD_LABEL[method ?? "none"] ?? method ?? "—";
}
