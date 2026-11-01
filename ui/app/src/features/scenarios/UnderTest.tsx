import { Link } from "react-router-dom";
import { useBook, useScenarioCatalog } from "@/lib/api";
import { humanizeUsd, exactUsd } from "@/lib/format";
import { cn } from "@/lib/cn";

// "What is being stressed": a one-line strip on every stress page naming the book the
// scenarios ran against, so a loss is never read without its denominator. The book is
// requested at the scenario run's valuation date; the BFF falls back to its latest priced
// date when that one is missing, and the strip says so rather than implying a match.

export function UnderTest() {
  const catalog = useScenarioCatalog();
  const scenarioDate = catalog.data?.valuation_date ?? undefined;
  const book = useBook(scenarioDate);
  const b = book.data;

  if (!b?.valuation_date) return null;
  const mismatch = scenarioDate != null && b.valuation_date !== scenarioDate;

  return (
    <Link
      to="/pricing"
      className={cn(
        "mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-xs hover:bg-surface-0",
        mismatch ? "border-warning bg-warning-tint" : "bg-surface-1",
      )}
    >
      <span className="font-semibold uppercase tracking-wider text-fg-muted">Testing</span>
      <span className="text-fg">{b.trades.length} trades</span>
      <span className="text-fg-muted">·</span>
      <span className="tnum text-fg" title={exactUsd(b.total_pv)}>
        {humanizeUsd(b.total_pv)} PV
      </span>
      <span className="text-fg-muted">·</span>
      <span className="text-fg-secondary">priced {b.valuation_date}</span>
      {mismatch && (
        <span className="text-warning-fg">— scenarios ran on {scenarioDate}; no book valuation for that date</span>
      )}
      <span className="ml-auto text-fg-secondary">view portfolio ›</span>
    </Link>
  );
}
