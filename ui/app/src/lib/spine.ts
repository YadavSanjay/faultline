// The provenance "correlation spine" (CROSSCUTTING_UI §1): the content-addressed
// identifiers stamped on every artifact. Rendered as clickable, cross-linking chips by
// ProvenanceBadge. This module is the single source of their display metadata.

export type SpineKind =
  | "run_id"
  | "snapshot_id"
  | "factor_snapshot_id"
  | "portfolio_version"
  | "cov_id"
  | "git_sha"
  | "config_hash";

export interface SpineMeta {
  label: string; // short human label
  title: string; // tooltip explanation
}

export const SPINE: Record<SpineKind, SpineMeta> = {
  run_id: { label: "run", title: "Run id — one execution of a module" },
  snapshot_id: { label: "snapshot", title: "Market-data snapshot — the exact input bytes priced" },
  factor_snapshot_id: { label: "factors", title: "Factor-history snapshot — VaR/covariance inputs" },
  portfolio_version: { label: "portfolio", title: "Portfolio version — the exact trade economics" },
  cov_id: { label: "cov", title: "Covariance id — the exact Σ used" },
  git_sha: { label: "git", title: "Code version (git sha) at run time" },
  config_hash: { label: "config", title: "Effective config hash" },
};

// Content-addressed ids are long hex/uuid; show a short, stable prefix.
export function shortId(value: string | null | undefined, n = 8): string {
  if (!value) return "—";
  const clean = value.replace(/-dirty$/, "");
  return clean.length > n ? clean.slice(0, n) : clean;
}
