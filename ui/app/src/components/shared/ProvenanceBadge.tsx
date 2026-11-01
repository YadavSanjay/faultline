import { SPINE, shortId, type SpineKind } from "@/lib/spine";
import { cn } from "@/lib/cn";

// A compact, copyable chip for a spine identifier (run_id, snapshot_id, …). The
// cross-linking/popover behavior arrives in later phases; Phase 0 ships label + short id
// + copy + tooltip so provenance is always one glance away.

export function ProvenanceChip({ kind, value, className }: {
  kind: SpineKind;
  value: string | null | undefined;
  className?: string;
}) {
  const meta = SPINE[kind];
  const copy = () => {
    if (value) void navigator.clipboard?.writeText(value);
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={`${meta.title}\n${value ?? "—"}\n(click to copy)`}
      className={cn(
        "inline-flex items-center gap-1 rounded border border bg-surface-2 px-1.5 py-0.5",
        "font-mono text-micro text-fg-secondary hover:bg-surface-0",
        className,
      )}
    >
      <span className="text-fg-muted">{meta.label}</span>
      <span className="tnum">{shortId(value)}</span>
    </button>
  );
}

export function ProvenanceBadge({ items, className }: {
  items: Partial<Record<SpineKind, string | null | undefined>>;
  className?: string;
}) {
  const entries = (Object.entries(items) as [SpineKind, string | null | undefined][]).filter(
    ([, v]) => v,
  );
  if (entries.length === 0) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {entries.map(([kind, value]) => (
        <ProvenanceChip key={kind} kind={kind} value={value} />
      ))}
    </div>
  );
}
