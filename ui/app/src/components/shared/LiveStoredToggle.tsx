import { cn } from "@/lib/cn";

// Live ⇄ Stored data-source toggle. By default it renders locked to "Stored" (a global
// indicator — most screens read stored runs). Pass `enableLive` + `onModeChange` to make it
// an interactive control (used on the trade page, which can re-price live via the service).

export function LiveStoredToggle({
  mode = "stored",
  onModeChange,
  enableLive = false,
}: {
  mode?: "stored" | "live";
  onModeChange?: (m: "stored" | "live") => void;
  enableLive?: boolean;
}) {
  const interactive = enableLive && !!onModeChange;

  if (!interactive) {
    return (
      <div
        className="inline-flex items-center rounded-md border border bg-surface-2 p-0.5 text-xs"
        title="This screen reads stored runs. Live re-price is on the trade page."
      >
        <span className={cn("rounded px-2 py-0.5 font-medium", mode === "stored" ? "bg-surface-1 text-fg shadow-sm" : "text-fg-muted")}>
          Stored
        </span>
        <span className="cursor-not-allowed px-2 py-0.5 text-fg-muted">Live</span>
      </div>
    );
  }

  return (
    <div className="inline-flex items-center rounded-md border border bg-surface-2 p-0.5 text-xs" title="Toggle live re-price (via the pricer service) vs the stored run">
      {(["stored", "live"] as const).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onModeChange?.(m)}
          className={cn(
            "rounded px-2 py-0.5 font-medium capitalize",
            mode === m ? "bg-surface-1 text-fg shadow-sm" : "text-fg-muted hover:text-fg-secondary",
          )}
        >
          {m}
        </button>
      ))}
    </div>
  );
}
