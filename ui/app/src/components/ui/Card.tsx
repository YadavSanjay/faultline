import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

// Lightweight Tailwind primitives. (shadcn/ui components can be layered in later; these
// keep Phase 0 buildable without the shadcn CLI — see BUILD_PLAN locked stack note.)

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("rounded-lg border border bg-surface-1 shadow-sm", className)}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, right }: {
  title: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-subtle px-4 py-3">
      <div>
        <h3 className="text-sm font-semibold text-fg">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-fg-secondary">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("px-4 py-3", className)}>{children}</div>;
}
