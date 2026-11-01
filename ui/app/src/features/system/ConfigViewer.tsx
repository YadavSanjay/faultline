import { useSystemConfig } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";

// S6 Config Explorer — model params (change-controlled) + operational settings (PRICER_*),
// secrets masked.
export function ConfigViewer() {
  const query = useSystemConfig();
  return (
    <QueryState query={query}>
      {(cfg) => (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Settings (operational)" subtitle="env overlay · PRICER_* · secrets masked" />
            <CardBody>
              <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
                {Object.entries(cfg.settings).map(([k, v]) => (
                  <span key={k}>
                    <span className="text-fg-muted">{k}:</span> <span className="font-mono text-fg">{v == null ? "—" : String(v)}</span>
                  </span>
                ))}
                <span>
                  <span className="text-fg-muted">upstream:</span> <span className="font-mono text-fg">{cfg.upstream_service}</span>
                </span>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Model config (change-controlled)" subtitle="configs/pricer/pricer_config.json" />
            <CardBody>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {Object.entries(cfg.model_config).map(([section, value]) => (
                  <div key={section} className="rounded-md border border-subtle">
                    <div className="border-b border-subtle px-3 py-1.5 text-xs font-semibold text-fg-secondary">{section}</div>
                    <pre className="max-h-64 overflow-auto px-3 py-2 text-micro leading-relaxed text-fg-secondary">
                      {typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}
                    </pre>
                  </div>
                ))}
              </div>
            </CardBody>
          </Card>

          <p className="text-xs text-fg-muted">{cfg.note}</p>
        </div>
      )}
    </QueryState>
  );
}
