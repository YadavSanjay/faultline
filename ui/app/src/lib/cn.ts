// Minimal className combiner (truthy strings only) — avoids a clsx dependency for now.
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
