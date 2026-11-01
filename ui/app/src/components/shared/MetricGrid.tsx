import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// MetricCell: value + unit + one-line plain-English caption (PRICING_UI §7). The caption
// is what makes the domain learnable by using the app.

export interface Metric {
  label: string;
  value: ReactNode;
  unit?: string;
  caption?: string;
  title?: string; // exact value / extra detail on hover
  tone?: "neutral" | "gain" | "loss";
}

const TONE: Record<NonNullable<Metric["tone"]>, string> = {
  neutral: "text-fg",
  gain: "text-success-fg",
  loss: "text-danger-fg",
};

export function MetricCell({ metric }: { metric: Metric }) {
  const tone = TONE[metric.tone ?? "neutral"];
  return (
    <div className="min-w-0" title={metric.title}>
      <div className="text-xs uppercase tracking-wide text-fg-muted">{metric.label}</div>
      <div className={cn("tnum text-lg font-semibold", tone)}>
        {metric.value}
        {metric.unit && <span className="ml-1 text-xs font-normal text-fg-muted">{metric.unit}</span>}
      </div>
      {metric.caption && <div className="mt-0.5 text-xs text-fg-secondary">{metric.caption}</div>}
    </div>
  );
}

export function MetricGrid({ metrics, columns = 4, className }: {
  metrics: Metric[];
  columns?: 2 | 3 | 4;
  className?: string;
}) {
  const cols = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <div className={cn("grid grid-cols-1 gap-4", cols, className)}>
      {metrics.map((m) => (
        <MetricCell key={m.label} metric={m} />
      ))}
    </div>
  );
}
