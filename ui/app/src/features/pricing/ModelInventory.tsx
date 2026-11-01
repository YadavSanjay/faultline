import { useModelInventory, type ModelEntry } from "@/lib/api";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";

// PR-B5 Model Inventory: the governed model per trade type — what we use and what it
// can/can't do — with portfolio coverage (a traded type with no entry is a gap).

function Bullets({ label, items, tone }: { label: string; items: string[]; tone: "slate" | "amber" }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="text-micro font-semibold uppercase tracking-wide text-fg-muted">{label}</div>
      <ul className={`mt-0.5 list-inside list-disc text-xs ${tone === "amber" ? "text-warning-fg" : "text-fg-secondary"}`}>
        {items.map((it) => (
          <li key={it}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

function ModelCard({ tradeType, m }: { tradeType: string; m: ModelEntry }) {
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-sm font-semibold text-fg">{m.name}</div>
            <div className="text-xs text-fg-muted">
              <span className="font-mono">{tradeType}</span> · {m.asset_class}
            </div>
          </div>
        </div>
        <div className="text-xs text-fg-secondary">
          <span className="text-fg-muted">model:</span> {m.model}
          <div className="mt-0.5">
            <span className="text-fg-muted">engine:</span> {m.engine}
          </div>
        </div>
        <Bullets label="Assumptions" items={m.assumptions} tone="slate" />
        <Bullets label="Limitations" items={m.limitations} tone="amber" />
        <Bullets label="IPV controls" items={m.ipv_controls} tone="slate" />
        <div className="rounded bg-surface-2 px-2 py-1 text-xs text-fg-secondary">
          <span className="text-fg-muted">approved use:</span> {m.approved_use}
        </div>
      </CardBody>
    </Card>
  );
}

export function ModelInventory({ embedded = false }: { embedded?: boolean }) {
  const query = useModelInventory();
  return (
    <div>
      {!embedded && <PageHeader title="Model Inventory" subtitle="Governed model per instrument — what we use, and what it can't do." />}
      <QueryState query={query}>
        {(data) => (
          <div className="space-y-4">
            <Card>
              <CardBody className="flex items-center gap-3">
                <Zone
                  status={data.coverage.missing.length === 0 ? "green" : "red"}
                  label={data.coverage.missing.length === 0 ? "all covered" : `${data.coverage.missing.length} gaps`}
                />
                <span className="text-sm text-fg-secondary">
                  {data.n_models} governed models · {data.coverage.covered.length}/{data.coverage.n_traded} traded
                  types covered
                </span>
                {data.coverage.missing.length > 0 && (
                  <span className="text-xs text-danger-fg">missing: {data.coverage.missing.join(", ")}</span>
                )}
              </CardBody>
            </Card>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {Object.entries(data.models).map(([tt, m]) => (
                <ModelCard key={tt} tradeType={tt} m={m} />
              ))}
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}
