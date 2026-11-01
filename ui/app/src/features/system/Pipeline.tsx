import { Link } from "react-router-dom";
import { usePipeline, type PipelineSnapshot } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { categoryClass } from "@/components/shared/CategoryTag";
import { fmtTimestamp } from "@/lib/format";

// S4 Pipeline DAG — runs grouped by the market-data snapshot they consumed: which data
// version flowed into today's pricing/risk/scenario numbers.
function SnapshotRow({ snap }: { snap: PipelineSnapshot }) {
  const byModule = new Map<string, typeof snap.runs>();
  for (const r of snap.runs) {
    const g = byModule.get(r.module) ?? [];
    g.push(r);
    byModule.set(r.module, g);
  }
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-subtle px-4 py-2">
        <div className="flex items-center gap-2">
          <ProvenanceBadge items={{ snapshot_id: snap.snapshot_id }} />
          {snap.file_count != null && <span className="text-xs text-fg-muted">{snap.file_count} files</span>}
        </div>
        {snap.created_at && <span className="text-xs text-fg-muted">created {fmtTimestamp(snap.created_at)}</span>}
      </div>
      <div className="flex items-stretch gap-2 overflow-auto p-4">
        <div className="flex shrink-0 items-center rounded-md bg-cat-teal-tint px-3 text-xs font-medium text-cat-teal-fg">market-data snapshot</div>
        <div className="flex items-center text-fg-muted">→</div>
        {["pricer", "risk", "scenariogen"].map((mod) => {
          const runs = byModule.get(mod) ?? [];
          if (runs.length === 0) return null;
          return (
            <div key={mod} className="min-w-[160px] rounded-md border border p-2">
              <div className={`mb-1 inline-block rounded px-1.5 py-0.5 text-nano font-medium ${categoryClass(mod)}`}>{mod}</div>
              <div className="text-micro text-fg-muted">{runs.length} run(s)</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {runs.slice(0, 6).map((r) => (
                  <Link key={r.run_id} to={`/ops/trace/${r.run_id}`} title={r.run_id} className="rounded bg-surface-2 px-1 font-mono text-nano text-fg-secondary hover:bg-surface-0">
                    {r.run_id.slice(0, 6)}
                  </Link>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export function Pipeline() {
  const query = usePipeline();
  return (
    <QueryState query={query} empty={(d) => d.snapshots.length === 0}>
      {(data) => (
        <div className="space-y-3">
          <p className="text-xs text-fg-secondary">Each market-data snapshot and the pricing / risk / scenario runs that consumed it. Click a run to trace it.</p>
          {[...data.snapshots].sort((a, b) => b.runs.length - a.runs.length).map((s) => (
            <SnapshotRow key={s.snapshot_id} snap={s} />
          ))}
        </div>
      )}
    </QueryState>
  );
}
