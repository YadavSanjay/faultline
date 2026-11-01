// Typed client + React Query hooks for the BFF. Phase 0 covers system runs, the upstream
// health proxy, and the governance posture. Endpoint shapes mirror ui/bff/app.
//
// Later phases generate this from the BFF OpenAPI spec; hand-typed for the P0 surface.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const BASE = "/api";

async function getJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, { headers: { Accept: "application/json" } });
  if (!resp.ok) throw new Error(`${path} → ${resp.status} ${resp.statusText}`);
  return (await resp.json()) as T;
}

// --- gated writes (operator + token headers) ------------------------------- //

const OPERATOR_KEY = "qlp.operator";
const WRITE_TOKEN_KEY = "qlp.writeToken";

export function getOperator(): string {
  return localStorage.getItem(OPERATOR_KEY) || "local";
}
export function setOperator(name: string): void {
  localStorage.setItem(OPERATOR_KEY, name.trim() || "local");
}
export function getWriteToken(): string {
  return localStorage.getItem(WRITE_TOKEN_KEY) || "";
}
export function setWriteToken(token: string): void {
  localStorage.setItem(WRITE_TOKEN_KEY, token);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-QLP-Actor": getOperator(),
  };
  const token = getWriteToken();
  if (token) headers["X-QLP-Token"] = token;
  const resp = await fetch(`${BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const j = (await resp.json()) as { detail?: string };
      if (j.detail) detail = j.detail;
    } catch {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

// --- types ----------------------------------------------------------------- //

export interface RunCounts {
  n_trades?: number;
  n_ok?: number;
  n_blocked?: number;
  n_failed?: number;
  n_warnings?: number;
  n_drift_alerts?: number;
  [k: string]: number | undefined;
}

export interface RunRow {
  module: string;
  run_id: string;
  date: string;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  status: string;
  git_sha: string;
  snapshot_id: string;
  total_pv: number | null;
  counts: RunCounts;
}

export interface RunsResponse {
  runs: RunRow[];
  modules: string[];
  count: number;
}

export type TileStatus = "green" | "amber" | "red" | "unknown" | "not_run";

export interface PostureTile {
  status: TileStatus;
  headline: string;
  detail?: string;
  run_id?: string | null;
  date?: string | null;
  snapshot_id?: string | null;
}

export interface PostureResponse {
  valuation_date: string | null;
  overall: TileStatus;
  tiles: Record<string, PostureTile>;
}

export interface SystemHealth {
  upstream: "up" | "down";
  url: string;
  data?: { status: string; snapshot_id: string; quantlib_version: string };
  error?: string;
}

// --- hooks ----------------------------------------------------------------- //

export function useRuns(params: { limit?: number; module?: string } = {}) {
  const qs = new URLSearchParams();
  if (params.limit) qs.set("limit", String(params.limit));
  if (params.module) qs.set("module", params.module);
  const suffix = qs.toString() ? `?${qs}` : "";
  return useQuery({
    queryKey: ["system", "runs", params],
    queryFn: () => getJson<RunsResponse>(`/system/runs${suffix}`),
  });
}

export function usePosture(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["governance", "posture", valuationDate ?? null],
    queryFn: () => getJson<PostureResponse>(`/governance/posture${suffix}`),
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ["system", "health"],
    queryFn: () => getJson<SystemHealth>(`/system/health`),
    refetchInterval: 30_000,
  });
}

// --- system / ops (Phase 5) ------------------------------------------------ //

export interface RunStage {
  stage: string;
  detail: string;
}

export interface SpanTimings {
  available: boolean;
  stages: { span: string; duration_ms: number }[];
  by_asset_class: { asset_class: string; n: number; total_ms: number; mean_ms: number }[];
  n_spans: number;
}

export interface RunTrace {
  available: boolean;
  reason?: string;
  run?: RunRow;
  extra?: Record<string, unknown>;
  stages: RunStage[];
  trace_level: string;
  spans?: SpanTimings;
  reproduce: { git_sha?: string; snapshot_id?: string; factor_snapshot_id?: string; command: string; recipe: string[] };
  trades?: { trade_id: string; asset_class: string; pv: number | null; status: string }[];
}

export interface PipelineSnapshot {
  snapshot_id: string;
  runs: { module: string; run_id: string; status: string; date: string; finished_at: string | null }[];
  file_count?: number;
  created_at?: string;
}

export interface ModuleNode {
  id: string;
  label: string;
  desc: string;
  route: string | null;
}

export interface ModuleMap {
  nodes: ModuleNode[];
  edges: { from: string; to: string }[];
  flow: string[];
}

export interface SystemConfig {
  model_config: Record<string, unknown>;
  settings: Record<string, unknown>;
  upstream_service: string;
  note: string;
}

export interface ProvenanceResult {
  query: Record<string, string | null>;
  runs: RunRow[];
  count: number;
}

export function useRunTrace(runId: string) {
  return useQuery({
    queryKey: ["system", "trace", runId],
    queryFn: () => getJson<RunTrace>(`/system/runs/${runId}`),
    enabled: !!runId,
  });
}

export function usePipeline(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({ queryKey: ["system", "pipeline", valuationDate ?? null], queryFn: () => getJson<{ valuation_date: string | null; snapshots: PipelineSnapshot[] }>(`/system/pipeline${suffix}`) });
}

export function useModules() {
  return useQuery({ queryKey: ["system", "modules"], queryFn: () => getJson<ModuleMap>(`/system/modules`) });
}

export function useSystemConfig() {
  return useQuery({ queryKey: ["system", "config"], queryFn: () => getJson<SystemConfig>(`/system/config`) });
}

export function useProvenance(params: { snapshot_id?: string; run_id?: string; portfolio_version?: string }) {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
  const key = qs.toString();
  return useQuery({
    queryKey: ["system", "provenance", key],
    queryFn: () => getJson<ProvenanceResult>(`/system/provenance?${key}`),
    enabled: !!key,
  });
}

// --- governance (Phase 6) -------------------------------------------------- //

export interface GoldenBreak {
  trade_id: string;
  expected: number;
  actual: number;
  abs_diff: number;
  rel_diff: number;
}

export interface IpvControl {
  trade_id: string;
  asset_class: string;
  control: string;
  kind: string;
  primary: number;
  independent: number | null;
  abs_diff: number;
  rel_diff: number;
  tolerance: number;
  passed: boolean;
}

export interface GovernanceReport {
  available: boolean;
  reason?: string;
  passed?: boolean;
  valuation_date?: string;
  run_id?: string;
  snapshot_id?: string;
  golden?: { passed: boolean; snapshot_matches: boolean; n_checked: number; n_breaks: number; breaks: GoldenBreak[]; missing: string[] };
  ipv?: { all_passed: boolean; n_total: number; n_failed: number; controls: IpvControl[] };
  model_inventory?: { covered: string[]; missing: string[] };
}

export interface DriftResponse {
  available: boolean;
  latest?: { run_id: string; date: string; finished_at: string | null; n_drift_alerts: number } | null;
  timeline: { run_id: string; date: string; finished_at: string | null; n_drift_alerts: number }[];
  note: string;
}

export function useGovernanceReport() {
  return useQuery({ queryKey: ["governance", "report"], queryFn: () => getJson<GovernanceReport>(`/governance/report`) });
}

export function useDrift() {
  return useQuery({ queryKey: ["governance", "drift"], queryFn: () => getJson<DriftResponse>(`/governance/drift`) });
}

// --- pricing (Phase 1) ----------------------------------------------------- //

export interface BookTrade {
  trade_id: string;
  description: string;
  asset_class: string;
  trade_type: string;
  currency: string;
  pv: number | null;
  prior_pv: number | null;
  delta: number | null;
  status: string;
  status_kind: "ok" | "blocked" | "failed";
  n_warnings: number;
  run_id: string | null;
  snapshot_id: string | null;
}

export interface AssetClassRow {
  asset_class: string;
  n_trades: number;
  total_pv: number | null;
}

export interface BookResponse {
  valuation_date: string | null;
  prior_date: string | null;
  available_dates: string[];
  total_pv: number;
  by_asset_class: AssetClassRow[];
  counts: { ok: number; blocked: number; failed: number; warnings: number };
  trades: BookTrade[];
}

export interface TradeProvenance {
  run_id?: string;
  git_sha?: string;
  config_hash?: string;
  market_data_snapshot_id?: string;
  portfolio_version?: string;
  pricer_class?: string;
  priced_at?: string;
  quantlib_version?: string;
  [k: string]: string | undefined;
}

export interface TradeResult {
  trade_id: string;
  trade_type: string;
  currency: string;
  valuation_date: string;
  pv: number;
  leg_pvs: Record<string, number>;
  measures: Record<string, number>;
  diagnostics: Record<string, string>;
  warnings: string[];
  provenance: TradeProvenance;
  asset_class: string;
  description: string;
  notional: number | null;
  params: Record<string, unknown>;
  available_dates: string[];
}

export interface PvHistoryPoint {
  valuation_date: string;
  pv: number | null;
  status: string;
  run_id: string;
  priced_at: string;
}

export function useBook(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["pricing", "book", valuationDate ?? null],
    queryFn: () => getJson<BookResponse>(`/pricing/book${suffix}`),
  });
}

export interface BookHistoryPoint {
  valuation_date: string;
  total_pv: number | null;
  n_trades: number;
  pnl: number | null;
}

export interface BookHistoryResponse {
  dates: string[];
  asset_classes: string[];
  total: BookHistoryPoint[];
  series: Record<string, (number | null)[]>;
}

export function useBookHistory() {
  return useQuery({
    queryKey: ["pricing", "book-history"],
    queryFn: () => getJson<BookHistoryResponse>(`/pricing/book/history`),
  });
}

export function useTrade(tradeId: string, valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["pricing", "trade", tradeId, valuationDate ?? null],
    queryFn: () => getJson<TradeResult>(`/pricing/trade/${tradeId}${suffix}`),
  });
}

export function usePvHistory(tradeId: string) {
  return useQuery({
    queryKey: ["pricing", "pv-history", tradeId],
    queryFn: () => getJson<{ trade_id: string; history: PvHistoryPoint[] }>(`/pricing/pv-history/${tradeId}`),
  });
}

export interface LiveResult {
  trade_id: string;
  trade_type: string;
  currency: string;
  valuation_date: string;
  pv: number;
  leg_pvs: Record<string, number>;
  measures: Record<string, number>;
  diagnostics: Record<string, string>;
  warnings: string[];
  provenance: TradeProvenance;
}

export interface LiveReprice {
  available: boolean;
  service: "up" | "down";
  url?: string;
  error?: string;
  trade_id?: string;
  priced_at?: string;
  snapshot_id?: string;
  result?: LiveResult;
}

export function useLiveReprice(tradeId: string, valuationDate: string | undefined, enabled: boolean) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["pricing", "live", tradeId, valuationDate ?? null],
    queryFn: () => getJson<LiveReprice>(`/pricing/live/${tradeId}${suffix}`),
    enabled: enabled && !!tradeId,
    staleTime: 0,
    gcTime: 0,
  });
}

// --- pricing methodology docs (docs/pricing/*) ----------------------------- //

export interface TradeDocSection {
  id: string;
  label: string;
  title: string;
  markdown: string;
}

export interface TradeDocsResponse {
  available: boolean;
  trade_id: string;
  trade_type: string;
  description: string;
  reason?: string;
  sections: TradeDocSection[];
}

export function useTradeDocs(tradeId: string) {
  return useQuery({
    queryKey: ["pricing", "trade-docs", tradeId],
    queryFn: () => getJson<TradeDocsResponse>(`/pricing/trade/${tradeId}/docs`),
    enabled: !!tradeId,
  });
}

// --- lineage (PR-B3) ------------------------------------------------------- //

export interface CurvePoints {
  tenors: string[];
  par_rates: number[];
  zero_rates: (number | null)[];
  discount_factors: (number | null)[];
  forward_rates: (number | null)[];
  tenors_used: string[];
}

export interface Cashflow {
  leg: string;
  pay_date: string;
  amount: number | null;
  discount_factor: number | null;
  pv: number | null;
  accrual?: number | null;
  rate?: number | null;
  notional?: number | null;
}

export interface Reconciliation {
  expr: string;
  value: number;
  ok: boolean;
}

export interface MarketInput {
  kind?: string;
  currency?: string;
  purpose?: string | null;
  index?: string | null;
  tenor?: string | null;
  key?: string;
  ticker?: string;
  spot?: number;
  as_of_date?: string;
  source?: string;
  dividend_yield?: number;
}

export interface LineageResponse {
  available: boolean;
  trade_id: string;
  trade_type: string;
  description?: string;
  reason?: string;
  currency?: string;
  valuation_date?: string;
  plain_english?: string;
  notional?: number | null;
  warnings?: string[];
  final?: {
    pv: number;
    leg_pvs: Record<string, number>;
    reconciliation: Reconciliation[];
    measures: Record<string, number | null>;
  };
  intermediates?: {
    curves: Record<string, CurvePoints>;
    cashflows: Cashflow[];
    computation?: { step: string; value: number | string | null }[];
    tables?: { title: string; columns: string[]; rows: Record<string, unknown>[] }[];
    hazard_curve?: HazardCurvePoint[];
    credit_spread_curve?: CreditCurvePoint[];
    vol_surface?: VolSurface | null;
  };
  inputs?: {
    market_data: MarketInput[];
    static: Record<string, unknown>;
    curve_as_of?: string;
    curve_source?: string;
  };
  provenance?: TradeProvenance & { market_data_snapshot_id?: string };
  sensitivity_lineage?: SensitivityLineage[];
}

export interface HazardCurvePoint {
  tenor: string;
  par_spread_bp: number;
  forward_hazard_bp: number;
  survival_prob: number;
}

export interface CreditCurvePoint {
  tenor: string;
  z_spread_bp: number;
}

export interface VolSurface {
  underlier: string;
  spot: number;
  expiries_days: number[];
  strikes: number[];
  iv_pct: number[][]; // rows = strikes, cols = expiries
  leg_points: { strike: number; expiry_days: number; iv_pct: number }[];
}

export interface SensitivityLineage {
  factor: string;
  base_pv: number;
  up_pv: number;
  down_pv: number;
  value: number;
  bump_unit: string;
  method: string;
}

export function useLineage(tradeId: string, valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["lineage", tradeId, valuationDate ?? null],
    queryFn: () => getJson<LineageResponse>(`/lineage/${tradeId}${suffix}`),
  });
}

// --- model inventory (PR-B5) ----------------------------------------------- //

export interface ModelEntry {
  name: string;
  asset_class: string;
  model: string;
  engine: string;
  assumptions: string[];
  limitations: string[];
  approved_use: string;
  ipv_controls: string[];
}

export interface ModelInventoryResponse {
  n_models: number;
  models: Record<string, ModelEntry>;
  coverage: { covered: string[]; missing: string[]; n_traded: number };
}

export function useModelInventory() {
  return useQuery({
    queryKey: ["governance", "model-inventory"],
    queryFn: () => getJson<ModelInventoryResponse>(`/governance/model-inventory`),
  });
}

// --- risk (Phase 2) -------------------------------------------------------- //

export interface Sensitivity {
  factor: string;
  value: number;
  bump_unit: string;
  method: string;
}

export interface TradeSensitivities {
  trade_id: string;
  asset_class: string;
  currency: string;
  valuation_date: string;
  pv: number;
  sensitivities: Sensitivity[];
  description?: string;
}

export interface ScenarioReport {
  scenario: string;
  description?: string;
  total_pnl: number;
  pnl_by_asset_class: Record<string, number>;
  trades?: { trade_id: string; asset_class: string; base_pv: number; scenario_pv: number; pnl: number }[];
}

export interface VarComponent {
  class: string;
  standalone_var: number;
  standalone_es: number;
  component_es: number; // Euler contribution to the book ES (sums to diversified ES)
  es_share: number;
}

export interface VarDecompositionLevel {
  diversified_var: number;
  diversified_es: number;
  sum_standalone_var: number;
  diversification_benefit: number; // sum(standalone VaR) − diversified VaR
  components: VarComponent[];
}

export interface VarDecomposition {
  classes: string[];
  by_level: Record<string, VarDecompositionLevel>;
}

export interface VarBlock {
  valuation_date: string;
  horizon_days: number;
  lookback_days: number;
  n_scenarios: number;
  book_sensitivities: Record<string, number>;
  var: Record<string, number>;
  es: Record<string, number>;
  factor_stdev: Record<string, number>;
  missing_factors: string[];
  pnl_vector?: number[];
  decomposition?: VarDecomposition;
  fhs?: FhsBlock;
  stressed?: StressedBlock;
  parametric?: ParametricBlock;
}

export interface ParametricBlock {
  method: string;
  sigma: number;
  mean: number;
  var: Record<string, number>;
  es: Record<string, number>;
}

export interface FhsBlock {
  lambda: number;
  n_scenarios: number;
  var: Record<string, number>;
  es: Record<string, number>;
  pnl_vector?: number[];
}

export interface StressedBlock {
  window_days: number;
  window_start: string;
  window_end: string;
  var: Record<string, number>;
  es: Record<string, number>;
}

export interface FullRevalVar {
  method: string;
  valuation_date: string;
  lookback_days: number;
  horizon_days: number;
  n_scenarios: number;
  base_pv: number;
  var: Record<string, number>;
  es: Record<string, number>;
  worst_day?: string;
  worst_pnl?: number;
}

export interface BacktestPoint {
  date: string;
  pnl: number;
  var: number; // rolling VaR loss threshold (positive $); exception when loss > var
  exception: boolean;
}

export interface Backtest {
  method: string;
  level: number;
  window: number;
  n_days: number;
  n_exceptions: number;
  expected_exceptions: number;
  exception_rate: number;
  zone: "green" | "amber" | "red";
  kupiec_lr: number;
  christoffersen_lr?: number;
  cc_lr?: number;
  timeline?: BacktestPoint[];
}

export interface PnlExplain {
  scenario: string;
  valuation_date: string;
  actual_total: number;
  explained_by_factor: Record<string, number>;
  residual_total: number;
  coverage: number;
  trades: { trade_id: string; asset_class: string; actual: number; explained: Record<string, number>; explained_total: number; residual: number }[];
}

export interface LimitBreach {
  name: string;
  kind: string;
  value: number;
  limit: number;
}

export interface CarryTheta {
  valuation_date: string;
  horizon_days: number;
  total_carry: number;
  carry_by_asset_class: Record<string, number>;
  trades: { trade_id: string; asset_class: string; pv: number; pv_forward: number; carry: number }[];
}

export interface JtdIssuer {
  issuer: string;
  bucket: string; // IG | HY | index | unrated
  jtd: number; // signed P&L on default (− = net loss)
  gross_notional: number;
  lgd: number;
  n_positions: number;
}

export interface Jtd {
  valuation_date: string;
  idr: number; // incremental default charge (sum of per-issuer net losses)
  total_net_jtd: number;
  worst_issuer: string | null;
  worst_jtd: number;
  jtd_by_bucket: Record<string, number>;
  issuers: JtdIssuer[];
  index_positions: JtdIssuer[];
}

export interface RiskReport {
  available: boolean;
  valuation_date: string | null;
  available_dates: string[];
  run_id?: string;
  snapshot_id?: string;
  factor_snapshot_id?: string;
  generated_at?: string;
  book_sensitivities?: Record<string, number>;
  sensitivities?: TradeSensitivities[];
  scenarios?: ScenarioReport[];
  var?: VarBlock;
  full_reval_var?: FullRevalVar | null;
  pnl_explain?: PnlExplain | null;
  backtest?: Backtest | null;
  backtest_full_reval?: Backtest | null;
  carry_theta?: CarryTheta | null;
  jtd?: Jtd | null;
  limit_breaches?: LimitBreach[];
}

export interface VarConstruction {
  available: boolean;
  reason?: string;
  valuation_date?: string;
  lookback_days?: number;
  horizon_days?: number;
  n_scenarios?: number;
  pnl_vector: number[];
  var: Record<string, number>;
  es: Record<string, number>;
  factor_stdev: Record<string, number>;
  book_sensitivities: Record<string, number>;
  missing_factors: string[];
  decomposition?: VarDecomposition;
  run_id?: string;
  snapshot_id?: string;
  factor_snapshot_id?: string;
}

export function useRiskReport(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["risk", "report", valuationDate ?? null],
    queryFn: () => getJson<RiskReport>(`/risk/report${suffix}`),
  });
}

export function useVarConstruction(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["risk", "var-construction", valuationDate ?? null],
    queryFn: () => getJson<VarConstruction>(`/risk/var-construction${suffix}`),
  });
}

export interface RiskLimit {
  name: string;
  kind: string;
  key: string; // config key (VaR level or sensitivity factor) — for edits
  observed: number;
  limit: number;
  utilization: number;
  headroom: number;
  breached: boolean;
}

export interface RiskLimits {
  available: boolean;
  valuation_date: string | null;
  available_dates: string[];
  run_id?: string;
  snapshot_id?: string;
  n_limits: number;
  n_breaches: number;
  max_utilization: number;
  limits: RiskLimit[];
}

export function useRiskLimits(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["risk", "limits", valuationDate ?? null],
    queryFn: () => getJson<RiskLimits>(`/risk/limits${suffix}`),
  });
}

export interface LimitEdit {
  kind: string;
  key: string;
  ceiling: number;
  note: string;
}

export function useLimitEditMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: LimitEdit) =>
      postJson<{ ok: boolean; old_ceiling: number; new_ceiling: number }>(`/risk/limits`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["risk", "limits"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

// --- market data (Phase 3) ------------------------------------------------- //

export type DqVerdict = "ok" | "warn" | "block" | "ungated";

export interface Feed {
  id: string;
  asset_class: string;
  file: string;
  name: string;
  source: string;
  enabled: boolean;
  row_count: number | null;
  date_min: string | null;
  date_max: string | null;
  staleness_days: number | null;
  dq: DqVerdict;
  // real feed→consumption linkage (md_consumption): what actually reads the feed
  consumed_by: ConsumedBy;
  category: string;
  role: string;
  n_pv_trades: number;
  // legacy asset-class-level count (coarse; kept for back-compat)
  n_consumers: number;
  updated: string;
}

// coarse bucket for the catalog: both = PV + risk, pv, risk, none (reference/redundant/roadmap)
export type ConsumedBy = "both" | "pv" | "risk" | "none";

export interface FeedConsumption {
  consumed_by: ConsumedBy;
  category: string;
  role: string;
  pv_trades: { trade_id: string; description: string }[];
  n_pv_trades: number;
}

export interface FeedsResponse {
  feeds: Feed[];
  asset_classes: string[];
  count: number;
}

export interface DqCheck {
  check: string;
  severity: "ok" | "warn" | "block";
  message: string;
}

export interface FeedDetail {
  id: string;
  asset_class: string;
  file: string;
  name: string;
  source: Record<string, unknown>;
  provenance: {
    sha256: string | null;
    size_bytes: number;
    mtime: string;
    in_snapshot: boolean;
    row_count: number | null;
    columns: string[];
    date_min: string | null;
    date_max: string | null;
    staleness_days: number | null;
  };
  dq: { verdict: DqVerdict; checks: DqCheck[]; gated: boolean; note?: string };
  consumption: FeedConsumption;
  consumers: { trade_id: string; description: string }[];
}

export interface FeedSeries {
  id: string;
  columns: string[];
  date_col: string | null;
  numeric_columns: string[];
  rows: Record<string, unknown>[];
}

export interface Coverage {
  trades: { trade_id: string; description: string; asset_class: string }[];
  feeds_by_asset_class: Record<string, string[]>;
  granularity: string;
}

export interface DqDashboard {
  files: { id: string; verdict: DqVerdict; checks: DqCheck[]; staleness_days?: number | null; row_count?: number | null }[];
  counts: Record<string, number>;
  max_staleness_days: number | null;
}

export function useFeeds() {
  return useQuery({ queryKey: ["md", "feeds"], queryFn: () => getJson<FeedsResponse>(`/md/feeds`) });
}

export function useFeedDetail(feedId: string) {
  return useQuery({
    queryKey: ["md", "feed", feedId],
    queryFn: () => getJson<FeedDetail>(`/md/feeds/${feedId}`),
    enabled: !!feedId,
  });
}

export function useFeedSeries(feedId: string, limit = 500) {
  return useQuery({
    queryKey: ["md", "series", feedId, limit],
    queryFn: () => getJson<FeedSeries>(`/md/feeds/${feedId}/series?limit=${limit}`),
    enabled: !!feedId,
  });
}

export interface FeedRows {
  id: string;
  columns: string[];
  rows: Record<string, unknown>[];
  total: number;
  page: number;
  page_size: number;
  n_pages: number;
  filtered: boolean;
  error?: string;
}

export type SortDir = "asc" | "desc";

export function useFeedRows(
  feedId: string,
  page: number,
  pageSize: number,
  filters: Record<string, string>,
  sortCol: string | null,
  sortDir: SortDir,
) {
  const active: Record<string, string> = {};
  for (const [k, v] of Object.entries(filters)) if (v.trim()) active[k] = v.trim();
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (Object.keys(active).length) qs.set("filters", JSON.stringify(active));
  if (sortCol) {
    qs.set("sort_col", sortCol);
    qs.set("sort_dir", sortDir);
  }
  return useQuery({
    queryKey: ["md", "rows", feedId, page, pageSize, active, sortCol, sortDir],
    queryFn: () => getJson<FeedRows>(`/md/feeds/${feedId}/rows?${qs.toString()}`),
    enabled: !!feedId,
    placeholderData: (prev) => prev, // keep prior page visible while loading (no flicker)
  });
}

export function useCoverage() {
  return useQuery({ queryKey: ["md", "coverage"], queryFn: () => getJson<Coverage>(`/md/coverage`) });
}

export function useDq() {
  return useQuery({ queryKey: ["md", "dq"], queryFn: () => getJson<DqDashboard>(`/md/dq`) });
}

// --- option-implied signals (P6 / B6) -------------------------------------- //

export interface SignalSummary {
  underlying: string;
  snapshot_date: string;
  has_rnd: boolean;
  has_credit: boolean;
  merton_dd: number | null;
  atm_iv_30d?: number | null;
  atm_iv_90d?: number | null;
  term_ratio?: number | null;
  term_inverted?: boolean | null;
  skew_25d_30d?: number | null;
  vrp_20d?: number | null;
  realized_vol_20d?: number | null;
  pc_oi_ratio?: number | null;
  expected_move_front?: number | null;
  expected_move_front_dte?: number | null;
  prob_down_10?: number | null;
  prob_down_20?: number | null;
  prob_down_30?: number | null;
  prob_up_10?: number | null;
  [k: string]: string | number | boolean | null | undefined;
}

export interface SignalsResponse {
  names: SignalSummary[];
  snapshots: string[];
  count: number;
  note?: string;
}

export interface CreditImpliedRow {
  underlying: string;
  snapshot_date: string;
  merton_dd: number | null;
  merton_pd: number | null;
  cg_spread_bp: number | null;
  merton_spread_bp: number | null;
  cds_reference_bp: number | null;
  basis_bp: number | null;
  leverage: number | null;
  equity_vol: number | null;
}

export interface CreditImpliedResponse {
  rows: CreditImpliedRow[];
  columns?: string[];
  count: number;
  note?: string;
}

export interface SignalDetail {
  underlying: string;
  latest: Record<string, number | string | boolean | null>;
  history: Record<string, number | string | null>[];
  credit_implied: CreditImpliedRow | null;
  has_rnd: boolean;
}

export interface RndCurve {
  underlying: string;
  snapshot_date: string;
  strike: number[];
  pdf: number[];
  cdf: number[];
  rnd_spot?: number | null;
  rnd_forward?: number | null;
  rnd_dte?: number | null;
  expected_move_front?: number | null;
  prob_down_10?: number | null;
  prob_down_20?: number | null;
  prob_down_30?: number | null;
  prob_up_10?: number | null;
  prob_up_20?: number | null;
}

export function useOptionSignals() {
  return useQuery({ queryKey: ["signals", "list"], queryFn: () => getJson<SignalsResponse>(`/options/signals`) });
}

export function useSignalCredit() {
  return useQuery({ queryKey: ["signals", "credit"], queryFn: () => getJson<CreditImpliedResponse>(`/options/signals/credit`) });
}

export function useSignalDetail(name: string) {
  return useQuery({
    queryKey: ["signals", "detail", name],
    queryFn: () => getJson<SignalDetail>(`/options/signals/${name}`),
    enabled: !!name,
  });
}

export function useSignalRnd(name: string, enabled = true) {
  return useQuery({
    queryKey: ["signals", "rnd", name],
    queryFn: () => getJson<RndCurve>(`/options/signals/${name}/rnd`),
    enabled: !!name && enabled,
  });
}

// --- news & events (P6 / A5) ----------------------------------------------- //

export interface NewsItem {
  code: string;
  label: string;
}

export interface NewsEvent {
  ticker: string;
  cik: string;
  filing_date: string;
  form: string;
  item_codes: string;
  accession: string;
  doc_url: string;
  weight: number;
  items: NewsItem[];
}

export interface NewsEventsResponse {
  events: NewsEvent[];
  tickers: string[];
  item_labels: Record<string, string>;
  count: number;
  total?: number;
  date_max?: string;
  note?: string;
}

export interface NewsLeg {
  ticker: string;
  multiplier: number;
  event_score: number;
  tone_z: number | null;
  n_events: number;
  accessions: string[];
  item_codes: string[];
  applied: boolean;
  note: string;
}

export interface NewsGate {
  ok: boolean;
  newest_filing: string | null;
  staleness_days: number | null;
  n_names_with_events: number;
  reason: string;
}

export interface NewsCalibration {
  as_of: string;
  applied: boolean;
  gate: NewsGate;
  legs: Record<string, NewsLeg>;
  live_triggers: Record<string, string[]>;
  live_trigger_labels: Record<string, string>;
  earnings: Record<string, string[]>;
  note?: string;
}

export interface NewsNameDetail {
  ticker: string;
  as_of: string;
  events: NewsEvent[];
  salience: Record<string, unknown>[];
  leg: NewsLeg | null;
  gate: NewsGate | null;
}

export function useNewsEvents(ticker?: string) {
  const qs = ticker ? `?ticker=${encodeURIComponent(ticker)}` : "";
  return useQuery({
    queryKey: ["news", "events", ticker ?? "all"],
    queryFn: () => getJson<NewsEventsResponse>(`/news/events${qs}`),
  });
}

export function useNewsCalibration(asOf?: string) {
  const qs = asOf ? `?as_of=${asOf}` : "";
  return useQuery({
    queryKey: ["news", "calibration", asOf ?? "today"],
    queryFn: () => getJson<NewsCalibration>(`/news/calibration${qs}`),
  });
}

export interface MdOverviewFeed {
  id: string;
  asset_class: string;
  name: string;
  dq: DqVerdict;
  staleness_days: number | null;
  date_max: string | null;
  consumed_by: ConsumedBy;
  category: string;
  role: string;
  n_pv_trades: number;
  stale: boolean;
}

export interface MdOverview {
  n_feeds: number;
  n_asset_classes: number;
  n_consumed: number;
  n_unused: number;
  n_stale: number;
  max_staleness_days: number;
  stale_cutoff_days: number | null;
  dq_counts: Record<DqVerdict, number>;
  consumption_counts: Record<ConsumedBy, number>;
  by_asset_class: { asset_class: string; n_feeds: number; n_consumed: number; n_stale: number; worst_dq: DqVerdict }[];
  feeds: MdOverviewFeed[];
}

export interface MdDependencyFeed {
  id: string;
  asset_class: string;
  name: string;
  consumed_by: ConsumedBy;
  category: string;
  role: string;
  dq: DqVerdict;
  staleness_days: number | null;
  is_risk_factor: boolean;
  trades: { trade_id: string; description: string }[];
  n_trades: number;
}

export interface MdDependency {
  feeds: MdDependencyFeed[];
  trades: { trade_id: string; description: string; asset_class: string; feeds: string[] }[];
  n_feeds_consumed: number;
  n_trades: number;
}

export function useMdOverview() {
  return useQuery({ queryKey: ["md", "overview"], queryFn: () => getJson<MdOverview>(`/md/overview`) });
}

export function useMdDependency() {
  return useQuery({ queryKey: ["md", "dependency"], queryFn: () => getJson<MdDependency>(`/md/dependency`) });
}

// --- stress scenarios (Phase 4) -------------------------------------------- //

export interface DisplayMove {
  unit: string;
  value: number;
}

export interface ScenarioCell {
  scenario: string;
  severity: string;
  narrative: string;
  horizon_days: number | null;
  ran: boolean;
  /** ran | held_out (plausibility gate) | stopped (fail-loud) | not_run — from the newest stress report. */
  outcome?: TierOutcome;
  total_pnl: number | null;
  capital_delta: number | null;
  plausibility_status: string | null;
  scenario_version: string;
  used_macro: boolean;
  n_trades: number;
  signoff: string;
}

export interface LlmScenarioCell {
  scenario: string;
  severity: string;
  source: string;
  total_pnl: number | null;
  capital_delta: number | null;
  plausibility_status: string | null;
  scenario_version: string;
  n_trades: number;
  world_view_id: string;
  mode: string;
  run_id: string;
  total_cost_usd: number | null;
  trigger: string;
}

export interface ScenarioCatalogResponse {
  valuation_date: string | null;
  available_dates: string[];
  scenarios: string[];
  tiers: string[];
  cells: ScenarioCell[];
  llm_scenarios?: LlmScenarioCell[];
  covariance: { factors?: string[]; n_obs?: number; window?: string; cov_id?: string };
  run_id?: string;
  snapshot_id?: string;
  generated_at?: string;
  /** Stress-loss thresholds from configs/scenariogen/limits.json (USD, positive); null when unset. */
  limits?: { loss_limit: number | null; loss_warning: number | null };
}

export interface LlmProvenance {
  run_id?: string;
  mode?: string;
  models?: Record<string, string>;
  world_view_id?: string;
  narrative?: string;
  trigger?: string;
  total_cost_usd?: number;
  n_llm_calls?: number;
  n_tool_calls?: number;
  legs?: { factor: string; origin: string; n_citations: number }[];
}

export interface VectorEntry {
  factor: string;
  move: number;
  origin: "driver" | "overlay" | "derived" | "macro";
  unit: string;
  display: number;
}

export interface PlausibilityFinding {
  factor: string;
  check: string;
  observed: number;
  limit: number;
  severity: string;
}

export interface Plausibility {
  scenario: string;
  status: string;
  promotable: boolean;
  findings: PlausibilityFinding[];
}

export interface ScenarioCapital {
  level: number;
  es_base: number;
  es_shocked: number;
  capital_delta: number;
  lookback_days: number;
}

export interface ScenarioDetailResponse {
  available: boolean;
  reason?: string;
  scenario: string;
  severity: string;
  source?: string;
  llm?: LlmProvenance | null;
  narrative_available?: boolean;
  attribution_available?: boolean;
  trace_available?: boolean;
  narrative: string;
  trigger?: string;
  method?: string;
  horizon_days?: number;
  severity_scale?: number;
  scenario_version?: string;
  valuation_date?: string;
  generated_at?: string;
  snapshot_id?: string;
  covariance?: { factors?: string[]; n_obs?: number; window?: string; cov_id?: string };
  drivers?: Record<string, DisplayMove>;
  overlays?: Record<string, DisplayMove>;
  vector?: VectorEntry[];
  plausibility?: Plausibility;
  capital?: ScenarioCapital | null;
  total_pnl?: number;
  signoff?: { status: string; approver?: string; recorded_at?: string; note?: string };
  macro?: { used_macro: boolean; summary?: string; world_view_id?: string; sources?: Record<string, unknown> };
  tiers?: string[];
  outcome?: TierOutcome;
  /** Replay window: the report's provenance block when the tier ran, else {start, end} of the definition. */
  replay?: ReplayWindow | null;
  /** Tier not run: the drivers / horizon to show (report > version store > config > unresolved). */
  definition?: ScenarioDefinition | null;
  /** Held-out tier: the plausibility-gate findings. */
  findings?: { check?: string; severity?: string; factor?: string; message?: string }[];
  /** Stopped scenario: the fail-loud reason. */
  stopped?: StoppedScenario;
}

export type TierOutcome = "ran" | "held_out" | "stopped" | "not_run";

export interface ReplayWindow {
  start: string;
  end: string;
  horizon_days?: number;
  n_observed?: number;
  excluded?: Record<string, { move: number; reason: string }>;
  partial?: Record<string, string>;
  instantaneous?: boolean;
}

export interface ScenarioDefinition {
  source: "report" | "version" | "config" | "unresolved";
  drivers: Record<string, number>;
  overlays: Record<string, number>;
  horizon_days: number | null;
  severity_scale: number | null;
  scaled: boolean;
  replay: ReplayWindow | null;
}

export interface ScenarioResultsResponse {
  available: boolean;
  scenario: string;
  severity: string;
  source?: string;
  total_pnl?: number;
  pnl_by_asset_class?: Record<string, number>;
  capital?: ScenarioCapital | null;
  trades?: { trade_id: string; asset_class: string; currency: string; base_pv: number; scenario_pv: number; pnl: number }[];
}

export interface CorrelationResponse {
  available: boolean;
  reason?: string;
  factors: string[];
  correlation: number[][];
  cov_id?: string;
  window?: string;
  n_obs?: number;
}

export interface ScenarioVersion {
  scenario_version: string;
  created_at: string;
  name: string;
  method: string;
  n_drivers: number;
  n_overlays: number;
  horizon_days: number;
  signoff: string;
}

export function useScenarioCatalog(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["scenarios", "catalog", valuationDate ?? null],
    queryFn: () => getJson<ScenarioCatalogResponse>(`/scenarios${suffix}`),
  });
}

export function useScenarioDetail(name: string, severity?: string) {
  const suffix = severity ? `?severity=${severity}` : "";
  return useQuery({
    queryKey: ["scenarios", "detail", name, severity ?? null],
    queryFn: () => getJson<ScenarioDetailResponse>(`/scenarios/${encodeURIComponent(name)}${suffix}`),
    enabled: !!name,
  });
}

export function useScenarioResults(name: string, severity?: string) {
  const suffix = severity ? `?severity=${severity}` : "";
  return useQuery({
    queryKey: ["scenarios", "results", name, severity ?? null],
    queryFn: () => getJson<ScenarioResultsResponse>(`/scenarios/${encodeURIComponent(name)}/results${suffix}`),
    enabled: !!name,
  });
}

export interface ScenarioNarrativeResponse {
  available: boolean;
  reason?: string;
  scenario: string;
  severity: string;
  run_id?: string;
  world_view_id?: string;
  mode?: string;
  cost?: { total_cost_usd?: number; n_llm_calls?: number; n_tool_calls?: number };
  markdown?: string;
}

export function useScenarioNarrative(name: string, severity?: string) {
  const suffix = severity ? `?severity=${severity}` : "";
  return useQuery({
    queryKey: ["scenarios", "narrative", name, severity ?? null],
    queryFn: () => getJson<ScenarioNarrativeResponse>(`/scenarios/${encodeURIComponent(name)}/narrative${suffix}`),
    enabled: !!name,
  });
}

// --- dual-RAG: value-add attribution + call trace + cost (P4/P5) ----------- //
export interface AttributionLegDetail {
  ticker?: string;
  risk_factor?: string;
  seed_move?: number;
  fiscal_years?: number[];
  macro_field?: string;
  rationale?: string;
  calibration?: {
    applied?: boolean; multiplier?: number; exposure?: number | null; concept?: string;
    numerator?: number | null; denominator?: number | null; note?: string;
  } | null;
}

export interface AttributionLeg {
  factor: string;
  role: string;
  final_move: number;
  baseline_move: number | null;
  delta: number | null;
  n_citations: number;
  citations: string[];
  detail: AttributionLegDetail;
}

export interface ServiceContribution {
  service: string;
  applied: boolean;
  status: string;
  n_legs: number;
  n_citations: number;
  world_view_id?: string;
  gate_passed?: boolean | null;
  gate_findings?: string[];
  summary?: string;
  legs: AttributionLeg[];
}

export interface AttributionNarrative {
  text: string;
  model: string;
  cost_usd: number;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
}

export interface ScenarioAttribution {
  scenario: string;
  authored_baseline: { drivers: Record<string, number>; overlays: Record<string, number> };
  macrorag: ServiceContribution;
  arag: ServiceContribution;
  headline_lines: string[];
  narrative?: AttributionNarrative | null;
}

export interface AttributionResponse {
  available: boolean;
  reason?: string;
  scenario: string;
  run_id?: string;
  severity?: string;
  attribution?: ScenarioAttribution;
  narrative?: AttributionNarrative | null;
}

export interface TraceCallSlim {
  seq: number;
  service: string;
  purpose: string;
  ok: boolean;
  latency_ms: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  prompt_hash: string;
  error: string;
  has_request: boolean;
  has_response: boolean;
  has_prompt: boolean;
}

export interface TraceServiceRollup {
  n_calls: number;
  n_errors: number;
  cost_usd: number;
  latency_ms: number;
}

export interface TraceResponse {
  available: boolean;
  reason?: string;
  scenario: string;
  run_id?: string;
  n_calls?: number;
  total_cost_usd?: number;
  total_latency_ms?: number;
  by_service?: Record<string, TraceServiceRollup>;
  calls?: TraceCallSlim[];
}

export interface TraceCallFull extends TraceCallSlim {
  scenario?: string;
  system?: string;
  user?: string;
  request?: string;
  response?: string;
}

export interface TraceCallResponse {
  available: boolean;
  reason?: string;
  scenario: string;
  run_id?: string;
  seq?: number;
  call?: TraceCallFull;
}

export function useScenarioAttribution(name: string) {
  return useQuery({
    queryKey: ["scenarios", "attribution", name],
    queryFn: () => getJson<AttributionResponse>(`/scenarios/${encodeURIComponent(name)}/attribution`),
    enabled: !!name,
  });
}

export function useScenarioTrace(name: string) {
  return useQuery({
    queryKey: ["scenarios", "trace", name],
    queryFn: () => getJson<TraceResponse>(`/scenarios/${encodeURIComponent(name)}/trace`),
    enabled: !!name,
  });
}

export function useScenarioTraceCall(name: string, seq: number | null) {
  return useQuery({
    queryKey: ["scenarios", "trace-call", name, seq],
    queryFn: () => getJson<TraceCallResponse>(`/scenarios/${encodeURIComponent(name)}/trace/${seq}`),
    enabled: !!name && seq != null,
  });
}

export function useCorrelation() {
  return useQuery({ queryKey: ["scenarios", "correlation"], queryFn: () => getJson<CorrelationResponse>(`/scenarios/covariance`) });
}

export function useScenarioVersions() {
  return useQuery({ queryKey: ["scenarios", "versions"], queryFn: () => getJson<{ versions: ScenarioVersion[] }>(`/scenarios/versions`) });
}

export function useMacroHealth() {
  return useQuery({ queryKey: ["scenarios", "macro"], queryFn: () => getJson<{ enabled: boolean; status: string; base_url: string; note: string }>(`/scenarios/macro/health`) });
}

export interface SignoffRecord {
  status: string;
  approver?: string;
  recorded_at?: string;
  note?: string;
}

export function useScenarioSignoff() {
  return useQuery({
    queryKey: ["scenarios", "signoff"],
    queryFn: () => getJson<{ records: Record<string, SignoffRecord> }>(`/scenarios/signoff`),
  });
}

export interface SignoffWrite {
  scenario_version: string;
  scenario: string;
  status: "approved" | "rejected";
  note: string;
}

export function useSignoffMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SignoffWrite) => postJson<{ ok: boolean; record: SignoffRecord }>(`/scenarios/signoff`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenarios", "signoff"] });
      qc.invalidateQueries({ queryKey: ["scenarios", "versions"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

export interface AuditEntry {
  ts: string;
  action: string;
  target: string;
  actor: string;
  reason: string;
  outcome: string;
  details: Record<string, unknown>;
}

export function useAudit(limit = 50) {
  return useQuery({ queryKey: ["audit", limit], queryFn: () => getJson<{ entries: AuditEntry[] }>(`/audit?limit=${limit}`) });
}

export function useWriteMode() {
  return useQuery({ queryKey: ["write", "mode"], queryFn: () => getJson<{ mode: string }>(`/write/mode`) });
}

export interface RolesConfig {
  default_role: string;
  roles: Record<string, string>;
  action_roles: Record<string, string>;
}

const ROLE_LEVEL: Record<string, number> = { viewer: 0, approver: 1, operator: 2, admin: 3 };

export function useRoles() {
  return useQuery({ queryKey: ["roles"], queryFn: () => getJson<RolesConfig>(`/roles`) });
}

export function roleForOperator(cfg: RolesConfig | undefined, operator: string): string {
  if (!cfg) return "—";
  return cfg.roles[operator] || cfg.default_role;
}

export function canPerform(cfg: RolesConfig | undefined, operator: string, action: string): boolean {
  if (!cfg) return true;
  const role = roleForOperator(cfg, operator);
  const need = cfg.action_roles[action] ?? "operator";
  return (ROLE_LEVEL[role] ?? -1) >= (ROLE_LEVEL[need] ?? 99);
}

// --- job runner (gated run-triggering + reverse-stress solve) -------------- //

export interface JobCatalogEntry {
  key: string;
  label: string;
  cmd: string;
}

export interface JobRow {
  job_id: string;
  key: string;
  label: string;
  actor: string;
  note: string;
  status: string; // running | ok | failed
  started_at: string;
  finished_at: string | null;
  returncode: number | null;
}

export interface JobDetail extends JobRow {
  available: boolean;
  cmd?: string;
  log_tail?: string;
}

export function useJobCatalog() {
  return useQuery({ queryKey: ["jobs", "catalog"], queryFn: () => getJson<{ jobs: JobCatalogEntry[] }>(`/jobs/catalog`) });
}

export function useJobs() {
  return useQuery({
    queryKey: ["jobs", "list"],
    queryFn: () => getJson<{ jobs: JobRow[]; count: number; n_running: number }>(`/jobs`),
    refetchInterval: (query) => ((query.state.data?.n_running ?? 0) > 0 ? 2000 : false),
  });
}

export function useJobDetail(jobId: string | null) {
  return useQuery({
    queryKey: ["jobs", "detail", jobId],
    queryFn: () => getJson<JobDetail>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });
}

export interface JobLaunchReq {
  key: string;
  note: string;
}

export function useLaunchJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobLaunchReq) => postJson<{ ok: boolean; job: JobRow }>(`/jobs`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs", "list"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

// --- reverse stress (S5) --------------------------------------------------- //

export interface ReverseResult {
  scenario: string;
  description: string;
  reachable: boolean;
  severity_scale: number | null;
  achieved_loss: number;
  target_loss: number;
  iterations: number;
  n_drivers: number;
}

export interface ReverseResponse {
  available: boolean;
  reason?: string;
  valuation_date?: string | null;
  available_dates?: string[];
  run_id?: string;
  snapshot_id?: string;
  generated_at?: string;
  target_loss?: number;
  n_scenarios?: number;
  n_reachable?: number;
  results: ReverseResult[];
}

export interface ReverseDetail {
  available: boolean;
  scenario: string;
  description?: string;
  reason?: string;
  reachable?: boolean;
  severity_scale?: number;
  achieved_loss?: number;
  target_loss?: number;
  iterations?: number;
  drivers: string[];
  vector: Record<string, number>;
  provenance?: Record<string, unknown>;
}

export function useReverseStress(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["scenarios", "reverse", valuationDate ?? null],
    queryFn: () => getJson<ReverseResponse>(`/scenarios/reverse${suffix}`),
  });
}

export function useReverseDetail(name: string | null) {
  return useQuery({
    queryKey: ["scenarios", "reverse-detail", name],
    queryFn: () => getJson<ReverseDetail>(`/scenarios/reverse/${encodeURIComponent(name ?? "")}`),
    enabled: !!name,
  });
}

// --- layer ablation waterfall + signal health (step 15f) ------------------- //

export type WaterfallStepId = "macrorag" | "xbrlrag" | "events" | "market_intelligence";

export interface WaterfallStep {
  step: WaterfallStepId;
  layers: string[];
  pnl_before: number;
  pnl_after: number;
  delta: number;
  shapley: number;
  floor_hits: string[];
  n_calls: number | null;
  latency_s: number | null;
  cost_usd: number | null;
  contribution_per_s: number | null;
  contribution_per_usd: number | null;
}

export interface ScenarioWaterfall {
  scenario: string;
  severity: string;
  composition_rule: string;
  authored_pnl: number;
  final_pnl: number;
  authored_floor_hits: string[];
  steps: WaterfallStep[];
}

export interface AblationRun {
  run_id: string | null;
  severity: string | null;
  generated_at: string | null;
}

export interface StoppedScenario {
  scenario: string;
  reason: string;
  detail?: string;
}

interface AblationHeader {
  available: boolean;
  reason?: string;
  available_dates?: string[];
  runs?: AblationRun[];
  run_id?: string | null;
  valuation_date?: string | null;
  generated_at?: string | null;
  severity?: string | null;
  note?: string | null;
  composition_rules?: string[];
  mixed_rules?: boolean;
  stopped?: StoppedScenario[];
  overhead?: { n_calls?: number; latency_s?: number; cost_usd?: number | null } | null;
}

export interface AblationResponse extends AblationHeader {
  scenarios?: ScenarioWaterfall[];
}

export interface AblationScenarioResponse extends AblationHeader {
  scenario: string;
  waterfall?: ScenarioWaterfall;
}

export interface SignalSource {
  source: string;
  status: "down" | "degraded" | "ok" | "unused" | string;
  latest?: string | null;
  staleness_days?: number | null;
  stale?: boolean;
  covered?: number;
  expected?: number;
  coverage?: number | null;
  holds: Record<string, number>;
  n_holds: number;
}

export interface HeldOutTier {
  scenario: string;
  severity: string;
  scenario_version?: string;
  status?: string;
  findings: { check?: string; severity?: string; factor?: string; message?: string }[];
  n_findings: number;
  n_fail: number;
  n_flag: number;
}

export interface SignalHealthResponse {
  available: boolean;
  reason?: string;
  run_id: string | null;
  valuation_date: string | null;
  generated_at: string | null;
  overall: string | null;
  counts: Record<string, number> | null;
  sources: SignalSource[];
  held_out: HeldOutTier[];
  stopped: StoppedScenario[];
  attention: number;
  available_dates?: string[];
}

function querySuffix(params: Record<string, string | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(v as string)}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export function useAblation(params: { valuationDate?: string; severity?: string; runId?: string } = {}) {
  const suffix = querySuffix({ valuation_date: params.valuationDate, severity: params.severity, run_id: params.runId });
  return useQuery({
    queryKey: ["scenarios", "ablation", params.valuationDate ?? null, params.severity ?? null, params.runId ?? null],
    queryFn: () => getJson<AblationResponse>(`/scenarios/ablation${suffix}`),
  });
}

export function useAblationScenario(name: string, severity?: string) {
  const suffix = querySuffix({ severity });
  return useQuery({
    queryKey: ["scenarios", "ablation-scenario", name, severity ?? null],
    queryFn: () => getJson<AblationScenarioResponse>(`/scenarios/ablation/${encodeURIComponent(name)}${suffix}`),
    enabled: !!name,
  });
}

export function useSignalHealth(valuationDate?: string) {
  const suffix = querySuffix({ valuation_date: valuationDate });
  return useQuery({
    queryKey: ["scenarios", "signal-health", valuationDate ?? null],
    queryFn: () => getJson<SignalHealthResponse>(`/scenarios/signal-health${suffix}`),
  });
}

// --- measurement: coverage, decision impact, event replay ----------------- //

/** A report block that could not be shown (report missing, or its view raised). */
export interface MeasurementUnavailable {
  available: false;
  reason: string;
}

interface MeasurementHead {
  available: true;
  run_id: string | null;
  stress_run_id: string | null;
  generated_at: string | null;
  tier: string | null;
}

export interface CoverageDirection {
  index: number;
  variance_share: number;
  spanned_share: number;
  uncovered: boolean;
  top_loadings: { trade_id?: string; loading?: number }[];
}

export interface CoverageView extends MeasurementHead {
  coverage: number;
  n_directions: number | null;
  uncovered_share: number;
  directions: CoverageDirection[];
  diversity: { participation_ratio: number | null; target: number[] | null; in_target: boolean | null };
  losses: { scenario: string; loss: number }[];
  merge_pairs: { a: string; b: string; cosine: number }[];
  /** Scenarios with no P&L vector: names, or ``{scenario, reason}`` records. */
  missing: (string | { scenario?: string; reason?: string })[];
}

export interface DecisionBook {
  book: string;
  stress_budget: number | null;
  worst_scenario: string | null;
  worst_loss: number | null;
  binding_from: string | null;
  binding_to: string | null;
  n_over_budget: number;
  n_trades_changed: number | null;
}

export interface DecisionWithoutBook {
  book: string;
  binding_from: string | null;
  binding_to: string | null;
  binding_changed: boolean;
  size_ratio: number;
}

export interface DecisionView extends MeasurementHead {
  books: DecisionBook[];
  without: { source: string; books: DecisionWithoutBook[] }[];
}

export interface ReplayNearest {
  nearest: string | null;
  cosine: number | null;
}

export interface ReplayEpisode {
  name: string;
  as_of: string | null;
  horizon_days: number | null;
  authored: ReplayNearest;
  enriched: ReplayNearest;
  gain: number | null;
  available: string[] | null;
  dropped: string[];
  regime_asof: string | null;
  dropped_issuer_factors: string[];
}

export interface ReplayView extends MeasurementHead {
  limitations: string[];
  episodes: ReplayEpisode[];
}

export interface MeasurementResponse {
  valuation_date: string | null;
  available_dates: string[];
  coverage: CoverageView | MeasurementUnavailable;
  decision: DecisionView | MeasurementUnavailable;
  replay: ReplayView | MeasurementUnavailable;
}

export function useMeasurement(valuationDate?: string) {
  const suffix = querySuffix({ valuation_date: valuationDate });
  return useQuery({
    queryKey: ["scenarios", "measurement", valuationDate ?? null],
    queryFn: () => getJson<MeasurementResponse>(`/scenarios/measurement${suffix}`),
  });
}

// --- portfolio construction / position sizing ----------------------------- //

export interface SizingSize {
  trade_id: string;
  asset_class: string;
  description: string;
  is_hedge: boolean;
  multiplier: number;
  current_notional: number;
  target_notional: number;
  mu_annual: number;
  risk_contribution: number;
  risk_share: number;
  standalone_var99: number;
}

export interface SizingProfile {
  profile: string;
  label: string;
  capital_base: number;
  var_budget: number;
  achieved_var99: number;
  achieved_es975: number;
  expected_annual_return: number;
  expected_return_pct: number;
  portfolio_vol_annual: number;
  return_on_var: number;
  gross_leverage: number;
  net_notional: number;
  binding_constraint: string;
  sizes: SizingSize[];
}

export interface SizingRevaluation {
  label: string;
  total_pv: number;
  gross_notional: number;
  net_notional: number;
  var99: number;
  es975: number;
  economic_capital: number;
  stress_pnl: Record<string, number>;
  worst_stress_scenario: string;
  worst_stress_loss: number;
}

export interface ExpectedReturnRow {
  trade_id: string;
  mu_annual: number;
  method: string;
  detail: string;
}

export interface SizingResponse {
  available: boolean;
  valuation_date: string | null;
  available_dates: string[];
  run_id?: string;
  git_sha?: string;
  capital_base?: number;
  n_trades?: number;
  n_days?: number;
  n_factors?: number;
  profiles: Record<string, SizingProfile>;
  revaluation?: Record<string, SizingRevaluation>;
  expected_returns?: Record<string, ExpectedReturnRow>;
}

export interface SizingProfileDetail extends SizingProfile {
  available: boolean;
  valuation_date: string | null;
  run_id?: string;
  git_sha?: string;
  revaluation?: SizingRevaluation;
  expected_returns?: Record<string, ExpectedReturnRow>;
}

export function useSizing(valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["sizing", "report", valuationDate ?? null],
    queryFn: () => getJson<SizingResponse>(`/sizing${suffix}`),
  });
}

export function useSizingProfile(profile: string, valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["sizing", "profile", profile, valuationDate ?? null],
    queryFn: () => getJson<SizingProfileDetail>(`/sizing/${profile}${suffix}`),
    enabled: !!profile,
  });
}

export interface RebalanceTrade {
  trade_id: string;
  description: string;
  asset_class: string;
  is_hedge: boolean;
  multiplier: number;
  current_notional: number;
  target_notional: number;
  delta_notional: number;
  pct_change: number | null;
  direction: "increase" | "decrease" | "hold";
  risk_contribution: number;
  risk_share: number;
  standalone_var99: number;
}

export interface RebalanceResponse {
  available: boolean;
  profile: string;
  label?: string;
  valuation_date?: string | null;
  run_id?: string;
  capital_base?: number;
  binding_constraint?: string;
  var_budget?: number;
  achieved_var99?: number;
  gross_leverage?: number;
  gross_turnover?: number;
  n_increase?: number;
  n_decrease?: number;
  n_hold?: number;
  n_trades?: number;
  trades: RebalanceTrade[];
  available_profiles?: string[];
}

export interface BookVariant {
  available: boolean;
  profile: string;
  reason?: string;
  filename?: string;
  name?: string;
  description?: string;
  sizing_basis?: Record<string, unknown>;
  n_trades?: number;
  book?: unknown;
}

export function useSizingRebalance(profile: string, valuationDate?: string) {
  const suffix = valuationDate ? `?valuation_date=${valuationDate}` : "";
  return useQuery({
    queryKey: ["sizing", "rebalance", profile, valuationDate ?? null],
    queryFn: () => getJson<RebalanceResponse>(`/sizing/rebalance/${profile}${suffix}`),
    enabled: !!profile,
  });
}

export function useBookVariant(profile: string) {
  return useQuery({
    queryKey: ["sizing", "variant", profile],
    queryFn: () => getJson<BookVariant>(`/sizing/${profile}/variant`),
    enabled: !!profile,
  });
}

export interface ActiveBook {
  available: boolean;
  reason?: string;
  name?: string;
  profile?: string | null;
  label?: string;
  sizing_run_id?: string | null;
  n_trades?: number;
}

export function useActiveBook() {
  return useQuery({ queryKey: ["sizing", "active"], queryFn: () => getJson<ActiveBook>(`/sizing/active`) });
}

export interface ActivateReq {
  profile: string;
  note: string;
}

export function useActivateVariantMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ActivateReq) =>
      postJson<{ ok: boolean; profile: string; previous_profile: string | null; name: string; n_trades: number }>(
        `/sizing/activate`,
        body,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sizing", "active"] });
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
}

// --- LLM cost & operations (Phase 2) --------------------------------------- //

export interface StageRollup {
  n_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface LlmServiceRollup {
  n_calls: number;
  n_errors: number;
  cost_usd: number;
  latency_ms: number;
  total_tokens: number;
}

export interface LlmRunRow {
  run_id: string;
  mode: string;
  created_at: string;
  /** Provider profile the run used ("openai" for runs before multi-provider support). */
  profile: string;
  providers: string[];
  world_view_id: string;
  total_cost_usd: number;
  n_llm_calls: number;
  n_tool_calls: number;
  total_tokens: number;
  n_scenarios: number;
  over_budget?: boolean;
}

export interface LlmBudget {
  max_total_cost_usd: number;
  max_scenarios: number;
  max_tool_calls_per_phase: number;
  model_tiers: Record<string, string>;
  active_profile: string;
  source: string;
}

export interface LlmOpsSummary {
  budget: LlmBudget;
  llm: {
    n_runs: number;
    total_cost_usd: number;
    total_tokens: number;
    n_llm_calls: number;
    n_tool_calls: number;
    per_stage: Record<string, StageRollup>;
    per_provider: Record<string, StageRollup>;
    per_run: LlmRunRow[];
    max_run_cost_usd: number;
    n_over_budget: number;
  };
  rag: {
    n_traces: number;
    n_calls: number;
    total_cost_usd: number;
    total_latency_ms: number;
    by_service: Record<string, LlmServiceRollup>;
  };
  totals: { total_cost_usd: number; n_runs: number; n_traces: number };
  spend_over_time: { label: string; run_id: string; cost_usd: number }[];
}

export interface LlmRunDetail extends LlmRunRow {
  available: boolean;
  reason?: string;
  models: Record<string, string>;
  stage_providers: Record<string, string>;
  sdk_versions: Record<string, string | null>;
  discovery: string;
  ranking: { name: string; total_pnl: number; kept: boolean }[];
  scenarios: string[];
  cost: {
    total_cost_usd: number;
    total_prompt_tokens: number;
    total_completion_tokens: number;
    n_llm_calls: number;
    n_tool_calls: number;
    per_stage: Record<string, StageRollup>;
    per_provider: Record<string, StageRollup>;
  };
  budget: { max_total_cost_usd: number; over_budget: boolean };
}

export interface LlmTraceRow {
  valuation_date: string;
  run_id: string;
  n_calls: number;
  total_cost_usd: number;
  total_latency_ms: number;
  services: string[];
  n_errors: number;
}

export interface LlmTraceCall {
  seq: number;
  service: string;
  purpose: string;
  ok: boolean;
  latency_ms: number;
  model: string;
  scenario: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  error: string;
}

export interface LlmTraceDetail {
  available: boolean;
  reason?: string;
  run_id: string;
  valuation_date?: string;
  n_calls?: number;
  total_cost_usd?: number;
  total_latency_ms?: number;
  by_service?: Record<string, LlmServiceRollup>;
  calls?: LlmTraceCall[];
}

export interface RagService {
  service: string;
  role: string;
  enabled: boolean;
  status: string;
  base_url: string;
  breaker_threshold: number | null;
  timeout_s: number | null;
  max_attempts: number | null;
}

export interface RagHealthResponse {
  services: RagService[];
  note: string;
}

export function useLlmSummary() {
  return useQuery({ queryKey: ["llmops", "summary"], queryFn: () => getJson<LlmOpsSummary>(`/llmops/summary`) });
}

export function useLlmRuns() {
  return useQuery({
    queryKey: ["llmops", "runs"],
    queryFn: () => getJson<{ runs: LlmRunRow[]; count: number; budget: LlmBudget }>(`/llmops/runs`),
  });
}

export function useLlmRunDetail(runId: string | null) {
  return useQuery({
    queryKey: ["llmops", "run", runId],
    queryFn: () => getJson<LlmRunDetail>(`/llmops/runs/${runId}`),
    enabled: !!runId,
  });
}

export function useLlmTraces() {
  return useQuery({
    queryKey: ["llmops", "traces"],
    queryFn: () => getJson<{ traces: LlmTraceRow[]; count: number }>(`/llmops/traces`),
  });
}

export function useLlmTraceDetail(runId: string | null) {
  return useQuery({
    queryKey: ["llmops", "trace", runId],
    queryFn: () => getJson<LlmTraceDetail>(`/llmops/traces/${runId}`),
    enabled: !!runId,
  });
}

export function useRagHealth() {
  return useQuery({ queryKey: ["llmops", "health"], queryFn: () => getJson<RagHealthResponse>(`/llmops/health`) });
}
