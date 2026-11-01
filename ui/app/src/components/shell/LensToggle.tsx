import { useNavigate } from "react-router-dom";
import { landingFor, useLens, type Lens } from "@/lib/lens";
import { cn } from "@/lib/cn";

// Business ⇄ Technical toggle. Switching navigates to that lens's landing
// (business → Stress Overview, technical → Ops board).

const OPTIONS: { value: Lens; label: string }[] = [
  { value: "business", label: "Business" },
  { value: "technical", label: "Technical" },
];

export function LensToggle() {
  const { lens, setLens } = useLens();
  const navigate = useNavigate();

  return (
    <div className="inline-flex items-center rounded-md border border-strong bg-surface-1 p-0.5">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => {
            setLens(opt.value);
            navigate(landingFor(opt.value));
          }}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
            lens === opt.value
              ? "bg-primary text-primary-fg"
              : "text-fg-secondary hover:text-fg",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
