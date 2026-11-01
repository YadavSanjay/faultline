import { usePosture, type PostureTile, type TileStatus } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Zone, type ZoneStatus } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { cn } from "@/lib/cn";
import { SignalHealthBadge } from "@/features/scenarios/SignalHealth";

// Governance ▸ Control Room (G1): the trust posture, derived from stored runs + risk JSON
// (no engine calls in V1). Golden/IPV/inventory show "not run" until a live verify lands.

const TILE_LABEL: Record<string, string> = {
  run_health: "Run health",
  limits: "Risk limits",
  backtest: "VaR backtest",
  golden: "Golden regression",
  ipv: "Independent price verification",
  model_inventory: "Model inventory",
};

const TILE_ORDER = ["run_health", "limits", "backtest", "golden", "ipv", "model_inventory"];

const BORDER: Record<TileStatus, string> = {
  green: "border-success/30",
  amber: "border-warning/30",
  red: "border-danger/30",
  unknown: "border",
  not_run: "border-dashed border",
};

function Tile({ name, tile }: { name: string; tile: PostureTile }) {
  return (
    <div className={cn("rounded-lg border bg-surface-1 p-4 shadow-sm", BORDER[tile.status])}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-fg-secondary">{TILE_LABEL[name] ?? name}</span>
        <Zone status={tile.status as ZoneStatus} />
      </div>
      <div className="mt-2 text-base font-semibold text-fg">{tile.headline}</div>
      {tile.detail && <div className="mt-0.5 text-xs text-fg-secondary">{tile.detail}</div>}
      {tile.run_id && (
        <div className="mt-2">
          <ProvenanceBadge items={{ run_id: tile.run_id, snapshot_id: tile.snapshot_id }} />
        </div>
      )}
    </div>
  );
}

export function ControlRoom({ embedded = false }: { embedded?: boolean }) {
  const query = usePosture();

  return (
    <Card className={embedded ? "" : ""}>
      <QueryState query={query}>
        {(data) => (
          <div className="p-4">
            <div className="mb-4 flex items-center gap-3">
              <span className="text-sm font-semibold text-fg">Control Room</span>
              <Zone
                status={data.overall as ZoneStatus}
                label={`overall ${data.overall}`}
                className="text-sm"
              />
              {data.valuation_date && (
                <span className="text-xs text-fg-muted">val {data.valuation_date}</span>
              )}
              <SignalHealthBadge />
              <span className="ml-auto text-micro text-fg-muted">
                derived from stored runs · no live re-price
              </span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {TILE_ORDER.filter((k) => data.tiles[k]).map((k) => (
                <Tile key={k} name={k} tile={data.tiles[k]} />
              ))}
            </div>
          </div>
        )}
      </QueryState>
    </Card>
  );
}
