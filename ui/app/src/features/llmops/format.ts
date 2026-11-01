// LLM-cost formatting: micro-dollar amounts need more precision than the book's humanizeUsd
// (which rounds sub-$1k to cents). Latency is often minutes for RAG synthesis calls.

export function fmtCost(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (v === 0) return "$0";
  const abs = Math.abs(v);
  if (abs < 0.01) return `$${v.toFixed(5)}`;
  if (abs < 1) return `$${v.toFixed(4)}`;
  if (abs < 1000) return `$${v.toFixed(2)}`;
  if (abs < 1e6) return `$${(v / 1e3).toFixed(2)}k`;
  return `$${(v / 1e6).toFixed(2)}mm`;
}

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1e6) return `${(n / 1e3).toFixed(1)}k`;
  return `${(n / 1e6).toFixed(2)}M`;
}

export function fmtLatency(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

// service → calm accent (consistent across the Operations screens)
export const SERVICE_COLOR: Record<string, string> = {
  llm: "#7c3aed",
  macrorag: "#0f766e",
  arag: "#1d4ed8",
};

export function serviceColor(s: string): string {
  return SERVICE_COLOR[s] ?? "#64748b";
}
