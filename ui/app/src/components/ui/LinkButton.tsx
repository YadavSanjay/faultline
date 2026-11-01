import { Link } from "react-router-dom";
import { cn } from "@/lib/cn";

// A Link styled as a secondary button.
export function LinkButton({ to, children, className }: {
  to: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Link
      to={to}
      className={cn(
        "rounded border border-strong px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-2",
        className,
      )}
    >
      {children}
    </Link>
  );
}
