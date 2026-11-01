import { type ReactNode } from "react";
import { cn } from "@/lib/cn";

// Lightweight, dependency-free confirmation modal for gated write actions. Writes are
// visually distinct and require an explicit confirm (UI_DESIGN §5: safe-by-default).

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = "Confirm",
  tone = "primary",
  busy = false,
  disabled = false,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel?: string;
  tone?: "primary" | "danger";
  busy?: boolean;
  disabled?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      onClick={onCancel}
      role="presentation"
    >
      <div className="w-full max-w-md rounded-lg bg-surface-1 shadow-xl" onClick={(e) => e.stopPropagation()} role="dialog">
        <div className="border-b border-subtle px-4 py-3 text-sm font-semibold text-fg">{title}</div>
        <div className="space-y-3 px-4 py-3">{children}</div>
        {error && <div className="mx-4 rounded bg-danger-tint px-3 py-2 text-xs text-danger-fg">{error}</div>}
        <div className="flex justify-end gap-2 border-t border-subtle px-4 py-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-strong px-3 py-1.5 text-sm text-fg-secondary hover:bg-surface-2"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || disabled}
            className={cn(
              "rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50",
              tone === "danger" ? "bg-danger hover:bg-danger" : "bg-primary hover:bg-primary-hover",
            )}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
