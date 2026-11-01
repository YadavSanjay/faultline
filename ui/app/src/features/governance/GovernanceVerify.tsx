import { useGovernanceReport, type IpvControl, type GoldenBreak } from "@/lib/api";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { ProvenanceBadge } from "@/components/shared/ProvenanceBadge";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";
import { DataTable, type DataColumn } from "@/components/shared/table/DataTable";
import { humanizeUsd, exactUsd } from "@/lib/format";

const GOLDEN_COLUMNS: DataColumn<GoldenBreak>[] = [
  { key: "trade_id", header: "Trade", get: (b) => b.trade_id, filterable: true, render: (b) => <span className="font-mono text-fg">{b.trade_id}</span> },
  { key: "expected", header: "Expected", align: "right", get: (b) => b.expected, csv: (b) => b.expected, render: (b) => <span className="text-fg-secondary" title={exactUsd(b.expected)}>{humanizeUsd(b.expected)}</span> },
  { key: "actual", header: "Actual", align: "right", get: (b) => b.actual, csv: (b) => b.actual, render: (b) => <span className="text-danger-fg" title={exactUsd(b.actual)}>{humanizeUsd(b.actual)}</span> },
  { key: "rel_diff", header: "Rel diff", align: "right", get: (b) => b.rel_diff, csv: (b) => b.rel_diff, render: (b) => <span className="text-danger-fg">{(b.rel_diff * 100).toFixed(3)}%</span> },
];

const IPV_COLUMNS: DataColumn<IpvControl>[] = [
  { key: "trade_id", header: "Trade", get: (c) => c.trade_id, filterable: true, render: (c) => <span className="font-mono text-xs text-fg">{c.trade_id}</span> },
  { key: "control", header: "Control", get: (c) => c.control, filterable: true, render: (c) => <span className="text-xs text-fg-secondary">{c.control}</span> },
  { key: "kind", header: "Kind", get: (c) => c.kind, filterable: true, render: (c) => <span className="text-xs text-fg-muted">{c.kind}</span> },
  { key: "rel_diff", header: "Rel diff", align: "right", get: (c) => c.rel_diff, csv: (c) => c.rel_diff, render: (c) => <span className="text-xs text-fg-secondary">{(c.rel_diff * 100).toFixed(4)}%</span> },
  {
    key: "passed", header: "Result", get: (c) => (c.passed ? 1 : 0), csv: (c) => (c.passed ? "pass" : "FAIL"),
    render: (c) => <span className={c.passed ? "text-success-fg" : "text-danger-fg"}>{c.passed ? "pass ✓" : "FAIL ✗"}</span>,
  },
];

// G3 IPV + G4 Golden — the two price-verification gates, from the stored governance report.
export function GovernanceVerify() {
  const query = useGovernanceReport();
  return (
    <QueryState query={query} empty={(d) => !d.available}>
      {(r) => {
        if (!r.available) {
          return <WarningBanner tone="info" title="No governance run">{r.reason}</WarningBanner>;
        }
        const golden = r.golden!;
        const ipv = r.ipv!;
        return (
          <div className="space-y-4">
            <div className="flex items-center justify-end">
              <ProvenanceBadge items={{ run_id: r.run_id, snapshot_id: r.snapshot_id }} />
            </div>

            {/* golden */}
            <Card>
              <CardHeader
                title="Golden regression"
                subtitle="computed PVs vs the frozen baseline"
                right={<Zone status={golden.passed ? "green" : "red"} label={golden.passed ? "pass" : `${golden.n_breaks} breaks`} />}
              />
              <CardBody>
                <div className="mb-2 text-sm text-fg-secondary">
                  {golden.n_checked} trades checked · snapshot {golden.snapshot_matches ? "matches ✓" : "MISMATCH ✗"}
                </div>
                {golden.breaks.length === 0 ? (
                  <div className="text-sm text-fg-secondary">No PV drifted beyond tolerance — prices match the baseline.</div>
                ) : (
                  <DataTable columns={GOLDEN_COLUMNS} rows={golden.breaks} getRowKey={(b) => b.trade_id} pageSize={25} exportName="golden_breaks" dense />
                )}
                {golden.missing.length > 0 && <div className="mt-2 text-xs text-warning-fg">missing from this run: {golden.missing.join(", ")}</div>}
              </CardBody>
            </Card>

            {/* ipv */}
            <Card>
              <CardHeader
                title="Independent price verification"
                subtitle="each price checked by a second, independent route"
                right={<Zone status={ipv.all_passed ? "green" : "red"} label={`${ipv.n_total - ipv.n_failed}/${ipv.n_total} controls`} />}
              />
              <DataTable
                columns={IPV_COLUMNS}
                rows={[...ipv.controls].sort((a, b) => Number(a.passed) - Number(b.passed))}
                getRowKey={(c) => `${c.trade_id}-${c.control}`}
                rowClassName={(c) => (!c.passed ? "bg-danger-tint/40" : undefined)}
                pageSize={25}
                exportName="ipv_controls"
                dense
              />
            </Card>
          </div>
        );
      }}
    </QueryState>
  );
}
