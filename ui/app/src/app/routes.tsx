import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { useLens } from "@/lib/lens";
import { Cockpit } from "@/features/cockpit/Cockpit";
import { OpsBoard } from "@/features/ops/OpsBoard";
import { RunHistory } from "@/features/system/RunHistory";
import { SystemLayout } from "@/features/system/SystemLayout";
import { RunTrace } from "@/features/system/RunTrace";
import { Pipeline } from "@/features/system/Pipeline";
import { ModuleMap } from "@/features/system/ModuleMap";
import { Jobs } from "@/features/system/Jobs";
import { ConfigViewer } from "@/features/system/ConfigViewer";
import { LineageExplorer } from "@/features/lineage/LineageExplorer";
import { ControlRoom } from "@/features/governance/ControlRoom";
import { GovernanceLayout } from "@/features/governance/GovernanceLayout";
import { GovernanceVerify } from "@/features/governance/GovernanceVerify";
import { DriftPage } from "@/features/governance/DriftPage";
import { Admin } from "@/features/admin/Admin";
import { NotFound } from "@/features/Placeholder";
import { PricingLayout } from "@/features/pricing/PricingLayout";
import { PricingOverview } from "@/features/pricing/PricingOverview";
import { BookValuation } from "@/features/pricing/BookValuation";
import { TradeValuation } from "@/features/pricing/TradeValuation";
import { CalculationLineage } from "@/features/pricing/CalculationLineage";
import { PvHistory } from "@/features/pricing/PvHistory";
import { ModelInventory } from "@/features/pricing/ModelInventory";
import { RiskLayout } from "@/features/risk/RiskLayout";
import { RiskDashboard } from "@/features/risk/RiskDashboard";
import { RiskDrill } from "@/features/risk/RiskDrill";
import { VarFamily } from "@/features/risk/VarFamily";
import { RiskMethodology } from "@/features/risk/RiskMethodology";
import { PnlExplainPage } from "@/features/risk/PnlExplainPage";
import { BacktestPage } from "@/features/risk/BacktestPage";
import { LimitsPage } from "@/features/risk/LimitsPage";
import { MarketDataLayout } from "@/features/marketdata/MarketDataLayout";
import { MarketDataOverview } from "@/features/marketdata/MarketDataOverview";
import { FeedCatalog } from "@/features/marketdata/FeedCatalog";
import { FeedDetail } from "@/features/marketdata/FeedDetail";
import { Coverage } from "@/features/marketdata/Coverage";
import { DqDashboard } from "@/features/marketdata/DqDashboard";
import { SignalsLayout } from "@/features/signals/SignalsLayout";
import { SignalCatalog } from "@/features/signals/SignalCatalog";
import { SignalDetail } from "@/features/signals/SignalDetail";
import { CreditImplied } from "@/features/signals/CreditImplied";
import { NewsEvents } from "@/features/signals/NewsEvents";
import { ScenarioLayout } from "@/features/scenarios/ScenarioLayout";
import { StressOverview } from "@/features/scenarios/StressOverview";
import { ScenarioCatalog } from "@/features/scenarios/ScenarioCatalog";
import { ReverseStress } from "@/features/scenarios/ReverseStress";
import { LayerAblation } from "@/features/scenarios/LayerAblation";
import { SignalHealth } from "@/features/scenarios/SignalHealth";
import { Measurement } from "@/features/scenarios/Measurement";
import { ScenarioDetail } from "@/features/scenarios/ScenarioDetail";
import { Versions, SignoffPage } from "@/features/scenarios/Versions";
import { LlmOpsLayout } from "@/features/llmops/LlmOpsLayout";
import { LlmCostOverview } from "@/features/llmops/LlmCostOverview";
import { LlmRunExplorer } from "@/features/llmops/LlmRunExplorer";
import { RagHealth } from "@/features/llmops/RagHealth";
import { SizingLayout } from "@/features/sizing/SizingLayout";
import { SizingOverview } from "@/features/sizing/SizingOverview";
import { RebalanceBlotter } from "@/features/sizing/RebalanceBlotter";
import { Frontier } from "@/features/sizing/Frontier";
import { ProfileDetail } from "@/features/sizing/ProfileDetail";
import { Methodology } from "@/features/sizing/Methodology";

// The index route sends the user to the landing for their current lens
// (business → Stress Overview, technical → Ops board).
function LandingRedirect() {
  const { landing } = useLens();
  return <Navigate to={landing} replace />;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <LandingRedirect /> },
      { path: "cockpit", element: <Cockpit /> },
      { path: "overview", element: <StressOverview /> },
      {
        path: "ops",
        element: <SystemLayout />,
        children: [
          { index: true, element: <OpsBoard /> },
          { path: "runs", element: <RunHistory /> },
          { path: "jobs", element: <Jobs /> },
          { path: "pipeline", element: <Pipeline /> },
          { path: "modules", element: <ModuleMap /> },
          { path: "config", element: <ConfigViewer /> },
        ],
      },
      { path: "ops/trace/:runId", element: <RunTrace /> },
      {
        path: "governance",
        element: <GovernanceLayout />,
        children: [
          { index: true, element: <ControlRoom /> },
          { path: "verify", element: <GovernanceVerify /> },
          { path: "drift", element: <DriftPage /> },
          { path: "inventory", element: <ModelInventory embedded /> },
          { path: "signoff", element: <SignoffPage /> },
        ],
      },
      { path: "admin", element: <Admin /> },
      {
        path: "marketdata",
        element: <MarketDataLayout />,
        children: [
          { index: true, element: <MarketDataOverview /> },
          { path: "catalog", element: <FeedCatalog /> },
          { path: "coverage", element: <Coverage /> },
          { path: "dq", element: <DqDashboard /> },
        ],
      },
      { path: "marketdata/feed/*", element: <FeedDetail /> },
      {
        path: "signals",
        element: <SignalsLayout />,
        children: [
          { index: true, element: <SignalCatalog /> },
          { path: "credit", element: <CreditImplied /> },
          { path: "news", element: <NewsEvents /> },
        ],
      },
      { path: "signals/:name", element: <SignalDetail /> },
      {
        path: "pricing",
        element: <PricingLayout />,
        children: [
          { index: true, element: <PricingOverview /> },
          { path: "book", element: <BookValuation /> },
          { path: "inventory", element: <ModelInventory embedded /> },
        ],
      },
      { path: "pricing/trade/:tradeId", element: <TradeValuation /> },
      { path: "pricing/trade/:tradeId/lineage", element: <CalculationLineage /> },
      { path: "pricing/trade/:tradeId/history", element: <PvHistory /> },
      {
        path: "risk",
        element: <RiskLayout />,
        children: [
          { index: true, element: <RiskDashboard /> },
          { path: "drill", element: <RiskDrill /> },
          { path: "var-family", element: <VarFamily /> },
          { path: "methodology", element: <RiskMethodology /> },
          { path: "pnl-explain", element: <PnlExplainPage /> },
          { path: "backtest", element: <BacktestPage /> },
          { path: "limits", element: <LimitsPage /> },
        ],
      },
      {
        path: "scenarios",
        element: <ScenarioLayout />,
        children: [
          { index: true, element: <ScenarioCatalog /> },
          { path: "reverse", element: <ReverseStress /> },
          { path: "ablation", element: <LayerAblation /> },
          { path: "signal-health", element: <SignalHealth /> },
          { path: "measurement", element: <Measurement /> },
          { path: "versions", element: <Versions /> },
          { path: "signoff", element: <SignoffPage /> },
        ],
      },
      { path: "scenarios/:name", element: <ScenarioDetail /> },
      {
        path: "sizing",
        element: <SizingLayout />,
        children: [
          { index: true, element: <SizingOverview /> },
          { path: "rebalance", element: <RebalanceBlotter /> },
          { path: "frontier", element: <Frontier /> },
          { path: "methodology", element: <Methodology /> },
        ],
      },
      { path: "sizing/:profile", element: <ProfileDetail /> },
      {
        path: "operations",
        element: <LlmOpsLayout />,
        children: [
          { index: true, element: <LlmCostOverview /> },
          { path: "runs", element: <LlmRunExplorer /> },
          { path: "health", element: <RagHealth /> },
        ],
      },
      { path: "lineage", element: <LineageExplorer /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
