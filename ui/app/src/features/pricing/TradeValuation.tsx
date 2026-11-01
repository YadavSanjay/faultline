import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTrade } from "@/lib/api";
import { QueryState } from "@/components/shared/QueryState";
import { LiveStoredToggle } from "@/components/shared/LiveStoredToggle";
import { ValuationCard } from "./ValuationCard";
import { LiveReprice } from "./LiveReprice";
import { PricingMethodology } from "./PricingMethodology";

// PR-B2 Trade Valuation page: breadcrumb + the instrument-adaptive Valuation Card (stored),
// with an opt-in Live re-price panel (post-V1 write/live) that prices the trade now against
// the pricer service and compares it to the stored batch value.
export function TradeValuation() {
  const { tradeId = "" } = useParams();
  const query = useTrade(tradeId);
  const [mode, setMode] = useState<"stored" | "live">("stored");
  const trade = query.data;
  // B6: for an equity option, link to what the option market is pricing on its underlier.
  const underlier =
    trade?.trade_type === "equity_option" && typeof trade.params?.underlier === "string"
      ? (trade.params.underlier as string)
      : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <nav className="text-xs text-fg-secondary">
          <Link to="/pricing/book" className="hover:underline">
            Book
          </Link>
          {trade && (
            <>
              <span className="mx-1 text-fg-muted">›</span>
              <span className="capitalize">{trade.asset_class}</span>
              <span className="mx-1 text-fg-muted">›</span>
              <span className="font-mono text-fg-secondary">{tradeId}</span>
            </>
          )}
        </nav>
        <LiveStoredToggle mode={mode} onModeChange={setMode} enableLive />
      </div>

      {mode === "live" && trade && (
        <LiveReprice
          tradeId={tradeId}
          tradeType={trade.trade_type}
          storedPv={trade.pv}
          storedSnapshot={trade.provenance.market_data_snapshot_id}
          valuationDate={trade.valuation_date}
        />
      )}

      {underlier && (
        <div className="rounded-md border border bg-surface-2 px-4 py-2 text-xs text-fg-secondary">
          <Link to={`/signals/${underlier}`} className="font-medium text-primary hover:underline">
            Market Signals for {underlier} →
          </Link>{" "}
          what the option market is pricing (implied vol, skew, risk-neutral density and tail
          probabilities) on this trade's underlier.
        </div>
      )}

      <QueryState query={query}>{(t) => <ValuationCard trade={t} />}</QueryState>
      {tradeId && <PricingMethodology tradeId={tradeId} />}
    </div>
  );
}
