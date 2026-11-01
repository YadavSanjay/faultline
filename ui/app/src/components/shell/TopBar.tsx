import { useBook, useSystemHealth, type BookTrade } from "@/lib/api";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { Zone } from "@/components/shared/Zone";
import { LensToggle } from "./LensToggle";
import { ThemeToggle } from "./ThemeToggle";

// Top bar: brand, the valuation date + snapshot of the book the app is showing, the
// upstream-service health dot, the Stored/Live indicator, and the lens toggle.
//
// The date comes from the book (latest stored valuation date, each trade's latest result),
// not from the newest pricer run: a partial run (one asset class, an older date) must not
// relabel the whole app. The snapshot / run chips show only when the book has exactly one.

function single(trades: BookTrade[], key: "run_id" | "snapshot_id"): string | undefined {
  const ids = new Set(trades.map((t) => t[key]).filter((v): v is string => !!v));
  return ids.size === 1 ? [...ids][0] : undefined;
}

export function TopBar() {
  const { data: book } = useBook();
  const { data: health } = useSystemHealth();
  const trades = book?.trades ?? [];

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b border bg-surface-1 px-4">
      <div className="flex items-center gap-3">
        <div className="flex h-7 w-7 items-center justify-center rounded bg-primary text-xs font-bold text-primary-fg">
          FL
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-fg">Faultline</div>
          <div className="text-micro text-fg-secondary">Find where it breaks</div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {book?.valuation_date && (
          <div className="hidden items-center gap-2 md:flex">
            <span className="text-xs text-fg-secondary">val {book.valuation_date}</span>
            <ProvenanceBadge
              items={{ snapshot_id: single(trades, "snapshot_id"), run_id: single(trades, "run_id") }}
            />
          </div>
        )}
        <LiveStoredToggle />
        <Zone
          status={health?.upstream === "up" ? "green" : "unknown"}
          label={health?.upstream === "up" ? "service up" : "service offline"}
        />
        <LensToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
