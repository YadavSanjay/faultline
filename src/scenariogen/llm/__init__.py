"""LLM-driven scenario generation for scenariogen.

An agentic flow that reads the portfolio and generates correlated stress-test scenarios from
company fundamentals (ARag MCP) + macro factors (MacroRAG MCP), then narrates them with the LLM
invocation cost. See ``docs/design/scenariogen_llm_flow_design.md``.

P0 (this commit) is the scaffolding: config loading, the instrumented OpenAI gateway, cost
tracking, prompt management, per-run tracing, structured I/O models, and the artifact store. The
MCP client, phase logic, flow orchestration, runner, and UI land in later phases.
"""

from __future__ import annotations

from .config import Budgets, LLMSettings, RetryPolicy, load_llm_settings, load_mcp_settings, load_pricing_overrides
from .cost import MODEL_PRICING, CostTracker, UsageRecord, estimate_cost
from .flow import LLMScenarioFlow
from .gateway import (
    BudgetExceededError,
    ChatBackend,
    ChatBackendError,
    FatalChatError,
    InstrumentedChatClient,
    LLMCallResult,
    OpenAIChatBackend,
    RawCompletion,
    ToolCall,
    TransientChatError,
)
from .mcp import (
    McpClient,
    McpToolRegistry,
    RegisteredTool,
    ServerSpec,
    ToolListing,
    ToolLoopResult,
    ToolSource,
    parse_server_specs,
    run_tool_loop,
)
from .phases import (
    DiscoveryResult,
    EvidenceBundle,
    VerifyResult,
    calibrate_scenario,
    discover_factor_universe,
    extract_json_object,
    fetch_shock_schema,
    gather_evidence,
    narrate_scenario,
    propose_scenario,
    propose_scenarios,
    verify_scenario,
)
from .profiles import LLMConfig, backend_kwargs, build_chat_backend, check_api_keys, load_llm_config
from .prompts import PromptManager, PromptTemplate
from .provider import LLMScenarioProvider, slugify
from .results import PricedScenario, PricingService
from .schema import (
    CalibratedScenario,
    Citation,
    CompanyRiskFactor,
    CostSummary,
    DriverLeg,
    LLMScenarioArtifact,
    MacroRiskFactor,
    ScenarioProposal,
    ScenarioRanking,
)
from .store import LLMScenarioArtifactStore
from .trace import RunTrace, StageRollup, current_run, run_trace

__all__ = [
    "MODEL_PRICING",
    "BudgetExceededError",
    "Budgets",
    "CalibratedScenario",
    "ChatBackend",
    "ChatBackendError",
    "Citation",
    "CompanyRiskFactor",
    "CostSummary",
    "CostTracker",
    "DiscoveryResult",
    "DriverLeg",
    "EvidenceBundle",
    "FatalChatError",
    "InstrumentedChatClient",
    "LLMCallResult",
    "LLMConfig",
    "LLMScenarioArtifact",
    "LLMScenarioArtifactStore",
    "LLMScenarioFlow",
    "LLMScenarioProvider",
    "LLMSettings",
    "MacroRiskFactor",
    "McpClient",
    "McpToolRegistry",
    "OpenAIChatBackend",
    "PricedScenario",
    "PricingService",
    "PromptManager",
    "PromptTemplate",
    "RawCompletion",
    "RegisteredTool",
    "RetryPolicy",
    "RunTrace",
    "ScenarioProposal",
    "ScenarioRanking",
    "ServerSpec",
    "StageRollup",
    "ToolCall",
    "ToolListing",
    "ToolLoopResult",
    "ToolSource",
    "TransientChatError",
    "UsageRecord",
    "VerifyResult",
    "backend_kwargs",
    "build_chat_backend",
    "calibrate_scenario",
    "check_api_keys",
    "current_run",
    "discover_factor_universe",
    "estimate_cost",
    "extract_json_object",
    "fetch_shock_schema",
    "gather_evidence",
    "load_llm_config",
    "load_llm_settings",
    "load_mcp_settings",
    "load_pricing_overrides",
    "narrate_scenario",
    "parse_server_specs",
    "propose_scenario",
    "propose_scenarios",
    "run_tool_loop",
    "run_trace",
    "slugify",
    "verify_scenario",
]
