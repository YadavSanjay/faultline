import { useState } from "react";
import {
  useSystemConfig,
  useWriteMode,
  useRoles,
  roleForOperator,
  canPerform,
  getOperator,
  setOperator,
  getWriteToken,
  setWriteToken,
} from "@/lib/api";
import { useLens } from "@/lib/lens";
import { PageHeader } from "@/components/shared/PageHeader";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Zone } from "@/components/shared/Zone";
import { QueryState } from "@/components/shared/QueryState";
import { WarningBanner } from "@/components/shared/WarningBanner";

// Admin — environment, lens, write access, and posture.
export function Admin() {
  const cfg = useSystemConfig();
  const { lens, setLens } = useLens();
  const writeMode = useWriteMode();
  const roles = useRoles();
  const [operator, setOperatorState] = useState(getOperator);
  const [token, setTokenState] = useState(getWriteToken);
  const [saved, setSaved] = useState(false);
  const myRole = roleForOperator(roles.data, getOperator());

  const saveWrite = () => {
    setOperator(operator);
    setWriteToken(token);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="space-y-5">
      <PageHeader title="Admin" subtitle="Environment, preferences, and write access." />

      <Card>
        <CardHeader title="Lens" subtitle="Business ⇄ Technical" />
        <CardBody>
          <div className="inline-flex rounded-md border border-strong bg-surface-1 p-0.5">
            {(["business", "technical"] as const).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLens(l)}
                className={`rounded px-3 py-1 text-sm font-medium capitalize ${lens === l ? "bg-primary text-primary-fg" : "text-fg-secondary"}`}
              >
                {l}
              </button>
            ))}
          </div>
        </CardBody>
      </Card>

      <QueryState query={cfg}>
        {(c) => (
          <Card>
            <CardHeader title="Environment" subtitle="operational settings (secrets masked)" />
            <CardBody>
              <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
                {Object.entries(c.settings).map(([k, v]) => (
                  <span key={k}>
                    <span className="text-fg-muted">{k}:</span> <span className="font-mono text-fg">{v == null ? "—" : String(v)}</span>
                  </span>
                ))}
                <span><span className="text-fg-muted">upstream:</span> <span className="font-mono text-fg">{c.upstream_service}</span></span>
              </div>
            </CardBody>
          </Card>
        )}
      </QueryState>

      <Card>
        <CardHeader
          title="Write access"
          subtitle="operator identity + optional write token for gated writes (e.g. scenario sign-off)"
          right={<Zone status={writeMode.data?.mode === "token" ? "amber" : "green"} label={`writes: ${writeMode.data?.mode ?? "…"}`} />}
        />
        <CardBody className="space-y-3">
          <label className="block text-xs">
            <span className="text-fg-secondary">Operator name</span>
            <input
              value={operator}
              onChange={(e) => setOperatorState(e.target.value)}
              className="mt-0.5 w-64 rounded border border-strong px-2 py-1 text-sm"
              placeholder="your name"
            />
            <span className="ml-2 text-micro text-fg-muted">stamped on every write + audit record</span>
          </label>
          <label className="block text-xs">
            <span className="text-fg-secondary">Write token</span>
            <input
              value={token}
              onChange={(e) => setTokenState(e.target.value)}
              type="password"
              className="mt-0.5 w-64 rounded border border-strong px-2 py-1 text-sm"
              placeholder={writeMode.data?.mode === "token" ? "required — set QLP_WRITE_TOKEN value" : "not required in local-trust mode"}
            />
            <span className="ml-2 text-micro text-fg-muted">sent as X-QLP-Token; kept in this browser only</span>
          </label>
          <div className="flex items-center gap-3">
            <button type="button" onClick={saveWrite} className="rounded bg-primary px-3 py-1 text-sm font-medium text-primary-fg hover:bg-primary-hover">
              Save
            </button>
            {saved && <span className="text-xs text-success-fg">saved ✓</span>}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Roles &amp; permissions"
          subtitle="what the current operator can do (RBAC — configs/ui/roles.json)"
          right={<Zone status={myRole === "viewer" ? "amber" : "green"} label={`you: ${myRole}`} />}
        />
        <CardBody>
          {roles.data ? (
            <table className="w-full max-w-lg text-sm">
              <thead className="text-left text-xs text-fg-secondary">
                <tr>
                  <th className="py-1 font-medium">Write action</th>
                  <th className="py-1 font-medium">Requires</th>
                  <th className="py-1 font-medium">You</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {Object.entries(roles.data.action_roles).map(([action, need]) => {
                  const ok = canPerform(roles.data, getOperator(), action);
                  return (
                    <tr key={action}>
                      <td className="py-1 font-mono text-xs text-fg-secondary">{action}</td>
                      <td className="py-1 text-xs text-fg-secondary">{need}</td>
                      <td className={`py-1 text-xs ${ok ? "text-success-fg" : "text-danger-fg"}`}>{ok ? "allowed ✓" : "blocked ✗"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <div className="text-xs text-fg-muted">Loading role config…</div>
          )}
          <p className="mt-3 text-micro text-fg-muted">
            Roles: viewer &lt; approver &lt; operator &lt; admin. Default role{" "}
            <span className="font-mono">{roles.data?.default_role ?? "—"}</span>. Under local-trust the operator is
            self-declared, so this enforces role discipline + audit; it becomes an identity boundary once a write
            token / real auth is in place.
          </p>
        </CardBody>
      </Card>

      <WarningBanner tone="info" title="Posture">
        Read-mostly over stored runs. Gated writes are live for scenario sign-off (recorded to an audit trail); limit
        edits, apply-rebalance, run triggering and deploy-grade per-user auth remain post-V1. See ui/docs/BUILD_PLAN.md §15.
      </WarningBanner>
    </div>
  );
}
