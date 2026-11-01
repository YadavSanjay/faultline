import { useRagHealth, type RagService } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";

// RAG service posture: MacroRAG + ARag connection config, circuit-breaker thresholds, and
// where to find live fallback/breaker metrics. Config-level (no live probe — the services
// aren't assumed to be running).

function ServiceCard({ s }: { s: RagService }) {
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-subtle px-4 py-2">
        <h3 className="text-sm font-semibold text-fg">{s.service}</h3>
        <Zone status={s.enabled ? "green" : "unknown"} label={s.status} />
      </div>
      <CardBody className="space-y-2 text-sm">
        <div className="text-xs text-fg-secondary">{s.role}</div>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-fg-muted">Base URL</dt>
          <dd className="truncate font-mono text-fg-secondary" title={s.base_url}>{s.base_url || "—"}</dd>
          <dt className="text-fg-muted">Breaker threshold</dt>
          <dd className="tabular-nums text-fg-secondary">{s.breaker_threshold ?? "—"} consecutive failures</dd>
          <dt className="text-fg-muted">Timeout</dt>
          <dd className="tabular-nums text-fg-secondary">{s.timeout_s != null ? `${s.timeout_s}s` : "—"}</dd>
          <dt className="text-fg-muted">Max attempts</dt>
          <dd className="tabular-nums text-fg-secondary">{s.max_attempts ?? "—"}</dd>
        </dl>
      </CardBody>
    </Card>
  );
}

export function RagHealth() {
  const query = useRagHealth();
  return (
    <QueryState query={query}>
      {(data) => (
        <div className="space-y-5">
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {data.services.map((s) => (
              <ServiceCard key={s.service} s={s} />
            ))}
          </div>
          <WarningBanner tone="info" title="Live health &amp; fallback metrics">
            {data.note}
          </WarningBanner>
        </div>
      )}
    </QueryState>
  );
}
