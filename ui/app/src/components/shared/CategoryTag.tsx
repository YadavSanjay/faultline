import { cn } from "@/lib/cn";

// Categorical identity for modules and RAG services (NOT status). One source of
// truth so a given entity wears the same calm, theme-aware hue on every screen.
// Hues live as cat-* tokens (tailwind.config / index.css).

const HUE = {
  teal: "bg-cat-teal-tint text-cat-teal-fg",
  indigo: "bg-cat-indigo-tint text-cat-indigo-fg",
  violet: "bg-cat-violet-tint text-cat-violet-fg",
  amber: "bg-warning-tint text-warning-fg",
} as const;

const NEUTRAL = "bg-surface-2 text-fg-secondary";

// module / RAG-service → hue. Related entities intentionally rhyme
// (marketdata≈macrorag, pricer≈arag, risk≈llm).
const CATEGORY: Record<string, keyof typeof HUE> = {
  marketdata: "teal",
  macrorag: "teal",
  pricer: "indigo",
  arag: "indigo",
  risk: "violet",
  llm: "violet",
  scenariogen: "amber",
};

/** Pill color classes (bg + text) for a module/service name; neutral fallback. */
export function categoryClass(name: string): string {
  const hue = CATEGORY[name];
  return hue ? HUE[hue] : NEUTRAL;
}

export function CategoryTag({
  name,
  label,
  className,
}: {
  name: string;
  label?: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 text-nano font-medium capitalize",
        categoryClass(name),
        className,
      )}
    >
      {label ?? name}
    </span>
  );
}
