import { Link } from "react-router-dom";
import { useModules, type ModuleNode } from "@/lib/api";
import { Card, CardBody } from "@/components/ui/Card";
import { QueryState } from "@/components/shared/QueryState";
import { cn } from "@/lib/cn";

// S5 Module / Architecture Map — explore system parts and the data flow. Doubles as the
// per-subsystem Technical-lens entry (clickable nodes jump into each subsystem).

function NodeCard({ node }: { node: ModuleNode }) {
  const inner = (
    <div className={cn("h-full rounded-lg border bg-surface-1 p-3 shadow-sm transition-colors", node.route ? "border hover:border-strong" : "border-subtle")}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-fg">{node.label}</span>
        {node.route && <span className="text-nano text-fg-muted">open ▸</span>}
      </div>
      <p className="mt-1 text-xs text-fg-secondary">{node.desc}</p>
    </div>
  );
  return node.route ? <Link to={node.route}>{inner}</Link> : inner;
}

export function ModuleMap() {
  const query = useModules();
  return (
    <QueryState query={query}>
      {(data) => {
        const byId = new Map(data.nodes.map((n) => [n.id, n]));
        return (
          <div className="space-y-5">
            {/* data flow */}
            <Card>
              <div className="border-b border-subtle px-4 py-2 text-sm font-semibold text-fg">Data flow</div>
              <CardBody>
                <div className="flex flex-wrap items-center gap-2">
                  {data.flow.map((id, i) => {
                    const n = byId.get(id);
                    return (
                      <div key={id} className="flex items-center gap-2">
                        {i > 0 && <span className="text-fg-muted">→</span>}
                        {n?.route ? (
                          <Link to={n.route} className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-fg hover:bg-primary-hover">{n.label}</Link>
                        ) : (
                          <span className="rounded-md bg-surface-0 px-2.5 py-1 text-xs font-medium text-fg-secondary">{n?.label ?? id}</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </CardBody>
            </Card>

            {/* all components */}
            <div>
              <div className="mb-2 text-sm font-semibold text-fg-secondary">Components</div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {data.nodes.map((n) => (
                  <NodeCard key={n.id} node={n} />
                ))}
              </div>
            </div>
          </div>
        );
      }}
    </QueryState>
  );
}
