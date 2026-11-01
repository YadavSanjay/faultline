import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// Loud-but-not-blocking notice for staleness / data-quality / degraded states
// (PRICING_UI §7: "stale/blocked is loud, not hidden").

export function WarningBanner({ tone = "warn", title, children }: {
  tone?: "warn" | "error" | "info";
  title: ReactNode;
  children?: ReactNode;
}) {
  const styles = {
    warn: "border-warning/30 bg-warning-tint text-warning-fg",
    error: "border-danger/30 bg-danger-tint text-danger-fg",
    info: "border-info/30 bg-info-tint text-info-fg",
  }[tone];
  return (
    <div className={cn("rounded-md border px-3 py-2 text-sm", styles)}>
      <div className="font-medium">{title}</div>
      {children && <div className="mt-0.5 text-xs opacity-90">{children}</div>}
    </div>
  );
}
