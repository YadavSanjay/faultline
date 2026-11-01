import { useTheme } from "@/lib/theme";

// Light ⇄ Dark toggle. A single quiet icon button; the label is for a11y.
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-strong bg-surface-1 text-fg-secondary hover:text-fg"
    >
      <span aria-hidden className="text-sm leading-none">{dark ? "☀" : "☾"}</span>
    </button>
  );
}
