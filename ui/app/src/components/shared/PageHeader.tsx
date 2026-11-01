import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, right }: {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-fg-secondary">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}
