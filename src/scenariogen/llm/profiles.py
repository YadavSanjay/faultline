"""Provider profiles, model registry and governed pricing for the scenariogen LLM flow.

`llm.json` names the providers, a registry of models (each bound to one provider, with its output
cap, JSON mode and the effort / thinking / temperature rules it accepts) and named profiles that
map every stage to a model. `pricing.json` must price every model a profile uses (design D9). A
mistyped key or an illegal combination raises `ConfigError` when the config loads, never at the
first LLM call.

Spec: docs/specs/llm-provider-profiles.md. Design: docs/design/llm_multi_provider_design.md §6-§7.
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from src.common.config.env import expand_str
from src.common.config.models import ConfigError

from .gateway import ChatBackend, OpenAIChatBackend, RawCompletion

logger = logging.getLogger("scenariogen.llm.profiles")

STAGES = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")
PROVIDERS = ("openai", "anthropic", "deepseek")

# Effort levels each provider's backend sends; an openai model takes none (design §6).
_EFFORTS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "deepseek": frozenset({"none", "low", "high", "max"}),
}
_THINKING = frozenset({"adaptive", "between_tools", "disabled"})
# Thinking off (`between_tools` / `disabled`) is accepted only at effort `high` or below.
_THINKING_OFF_EFFORTS = frozenset({"low", "medium", "high"})
_RATE_KEYS = ("input", "output", "cached_input", "cache_write")
_WINDOW = re.compile(r"(\d{2}):(\d{2})-(\d{2}):(\d{2})")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ProviderSpec(_Strict):
    api_key_env: str
    timeout_s: float
    base_url: str | None = None


class _ModelSpec(_Strict):
    provider: str
    max_output_tokens: int
    json_mode: Literal["json_object", "json_schema"]
    temperature: bool = True
    effort: str | None = None
    thinking: str | None = None


class _StageParams(_Strict):
    max_tokens: int | None = None
    temperature: float | None = None


class _RetrySpec(_Strict):
    max_attempts: int
    min_wait_s: float
    max_wait_s: float


class _BudgetSpec(_Strict):
    max_tool_calls_per_phase: int
    max_total_cost_usd: float
    max_scenarios: int
    max_candidates: int


class _Doc(_Strict):
    active_profile: str
    providers: dict[str, _ProviderSpec]
    models: dict[str, _ModelSpec]
    profiles: dict[str, dict[str, str]]
    stage_params: dict[str, _StageParams]
    retry: _RetrySpec
    budgets: _BudgetSpec


class _Tier(_Strict):
    above_prompt_tokens: int
    input: float | None = None
    output: float | None = None
    cached_input: float | None = None
    cache_write: float | None = None


class _Peak(_Strict):
    weekdays_utc: list[str]
    input: float | None = None
    output: float | None = None
    cached_input: float | None = None
    cache_write: float | None = None


class _RateRow(_Strict):
    input: float
    output: float
    cached_input: float | None = None
    cache_write: float | None = None
    tiers: list[_Tier] | None = None
    peak: _Peak | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    min_wait_s: float = 1.0
    max_wait_s: float = 30.0


@dataclass(frozen=True)
class Budgets:
    """Cost / fan-out bounds for one generation run."""

    max_tool_calls_per_phase: int = 24
    max_total_cost_usd: float = 5.0
    max_scenarios: int = 10          # Mode 1: keep the top-N candidates by max loss
    max_candidates: int = 10         # Mode 1: how many candidate scenarios to propose+price


class LLMConfig:
    """Validated LLM configuration with the active profile resolved.

    Attributes:
        profile: The active profile name.
        retry: The gateway's retry policy.
        budgets: Cost / fan-out bounds for one run.
        pricing: The validated rate card (model name -> rate row), as `CostTracker` takes it.
        model_tiers: Stage -> model for the active profile (recorded in run manifests).
    """

    def __init__(
        self,
        *,
        profile: str,
        providers: dict[str, _ProviderSpec],
        models: dict[str, _ModelSpec],
        stage_models: dict[str, str],
        stage_params: dict[str, _StageParams],
        retry: RetryPolicy,
        budgets: Budgets,
        pricing: dict[str, Any],
    ):
        self.profile = profile
        self.retry = retry
        self.budgets = budgets
        self.pricing = pricing
        self.model_tiers = dict(stage_models)
        self._providers = providers
        self._models = models
        self._stage_params = stage_params

    def _stage_model(self, stage: str) -> _ModelSpec:
        return self._models[self.model_tiers[stage]]

    def model_for(self, stage: str) -> str:
        """Returns the model the active profile maps ``stage`` to.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The model name.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        return self.model_tiers[stage]

    def provider_for(self, stage: str) -> str:
        """Returns the provider of the stage's model.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The provider name.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        return self._stage_model(stage).provider

    def max_tokens_for(self, stage: str) -> int | None:
        """Returns the stage's ``max_tokens`` capped at its model's ``max_output_tokens``.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The capped output-token limit, or None when the stage sets none.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        spec = self._stage_model(stage)
        params = self._stage_params.get(stage)
        if params is None or params.max_tokens is None:
            return None
        if params.max_tokens > spec.max_output_tokens:
            logger.warning("stage %s max_tokens %d capped at %s's max_output_tokens %d",
                           stage, params.max_tokens, self.model_tiers[stage], spec.max_output_tokens)
            return spec.max_output_tokens
        return params.max_tokens

    def temperature_for(self, stage: str) -> float | None:
        """Returns the stage's temperature.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The temperature, or None when the stage sets none.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        self._stage_model(stage)  # KeyError for an unknown stage
        params = self._stage_params.get(stage)
        return params.temperature if params is not None else None

    def effort_for(self, stage: str) -> str | None:
        """Returns the ``effort`` of the stage's model.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The effort, or None when the model sets none.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        return self._stage_model(stage).effort

    def thinking_for(self, stage: str) -> str | None:
        """Returns the ``thinking`` mode of the stage's model.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            The thinking mode, or None when the model sets none.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        return self._stage_model(stage).thinking

    def json_mode_for(self, stage: str) -> str:
        """Returns the ``json_mode`` of the stage's model.

        Args:
            stage: One of the seven LLM-flow stages.

        Returns:
            ``"json_object"`` or ``"json_schema"``.

        Raises:
            KeyError: ``stage`` is not a known stage.
        """
        return self._stage_model(stage).json_mode

    def provider_of_model(self, model: str) -> str:
        """Returns the provider a registry model belongs to.

        Raises:
            KeyError: ``model`` is not in the registry.
        """
        return self._models[model].provider

    def model_options(self, provider: str) -> dict[str, dict[str, Any]]:
        """Per registry model of `provider`: its output cap, effort and thinking (for its backend)."""
        return {
            name: {"max_output_tokens": spec.max_output_tokens, "effort": spec.effort, "thinking": spec.thinking}
            for name, spec in self._models.items() if spec.provider == provider
        }

    def providers_in_use(self) -> list[str]:
        """Returns the sorted distinct providers across all seven stages of the active profile.

        Returns:
            Sorted provider names.
        """
        return sorted({self.provider_for(stage) for stage in STAGES})

    def provenance(self) -> dict[str, Any]:
        """Returns the active profile's provenance record for the run manifest.

        Returns:
            ``{"profile": ..., "stages": {stage: {"model", "provider"}}, "models": {model:
            {"provider", "effort", "thinking"}}}``, with only the models in use.
        """
        models: dict[str, dict[str, Any]] = {}
        for stage in STAGES:
            name = self.model_tiers[stage]
            spec = self._models[name]
            models[name] = {"provider": spec.provider, "effort": spec.effort, "thinking": spec.thinking}
        return {
            "profile": self.profile,
            "stages": {s: {"model": self.model_tiers[s], "provider": self.provider_for(s)} for s in STAGES},
            "models": models,
        }

    def run_record(self) -> dict[str, Any]:
        """`provenance()` plus the installed provider SDK versions, for the run manifest."""
        return {**self.provenance(), "sdk_versions": sdk_versions()}

    def with_budgets(self, **changes: Any) -> LLMConfig:
        """Returns a copy with some budget fields replaced (tests and ad-hoc runs)."""
        from dataclasses import replace

        return LLMConfig(
            profile=self.profile, providers=self._providers, models=self._models,
            stage_models=self.model_tiers, stage_params=self._stage_params, retry=self.retry,
            budgets=replace(self.budgets, **changes), pricing=self.pricing,
        )

    def provider_spec(self, provider: str) -> _ProviderSpec:
        """Returns a configured provider's settings.

        Raises:
            ConfigError: ``provider`` is not configured.
        """
        spec = self._providers.get(provider)
        if spec is None:
            raise ConfigError(f"llm.json: provider {provider!r} is not configured")
        return spec


def sdk_versions() -> dict[str, str | None]:
    """Installed versions of the provider SDKs (None when not installed)."""
    out: dict[str, str | None] = {}
    for package in ("openai", "anthropic"):
        try:
            out[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            out[package] = None
    return out


def _validate(model_cls: type[_Strict], data: Any, where: str) -> Any:
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{where}: {exc}") from exc


def _finite(value: float | None, where: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        bound = "> 0" if positive else ">= 0"
        raise ConfigError(f"{where} must be a finite number {bound}, got {value!r}")


def _check_window(window: str, where: str) -> None:
    m = _WINDOW.fullmatch(window)
    if m is None:
        raise ConfigError(f"{where}: peak window {window!r} is not HH:MM-HH:MM")
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        raise ConfigError(f"{where}: peak window {window!r} is not a valid 24-hour time range")
    if h1 * 60 + m1 >= h2 * 60 + m2:
        raise ConfigError(f"{where}: peak window {window!r} must start before it ends")


def _validate_pricing(pricing: dict[str, Any]) -> None:
    for model, raw in pricing.items():
        where = f"pricing.json[{model}]"
        row: _RateRow = _validate(_RateRow, raw, where)
        if row.tiers is not None and row.peak is not None:
            raise ConfigError(f"{where}: a row may not have both tiers and peak")
        for key in _RATE_KEYS:
            _finite(getattr(row, key), f"{where}.{key}")
        for i, tier in enumerate(row.tiers or []):
            if tier.above_prompt_tokens <= 0:
                raise ConfigError(f"{where}.tiers[{i}].above_prompt_tokens must be > 0")
            for key in _RATE_KEYS:
                _finite(getattr(tier, key), f"{where}.tiers[{i}].{key}")
        if row.peak is not None:
            if not row.peak.weekdays_utc:
                raise ConfigError(f"{where}.peak.weekdays_utc must list at least one window")
            for window in row.peak.weekdays_utc:
                _check_window(window, f"{where}.peak")
            for key in _RATE_KEYS:
                _finite(getattr(row.peak, key), f"{where}.peak.{key}")


def _validate_model(name: str, spec: _ModelSpec, providers: Mapping[str, _ProviderSpec]) -> None:
    where = f"llm.json models[{name}]"
    if spec.provider not in providers:
        raise ConfigError(f"{where}: provider {spec.provider!r} is not in providers")
    if spec.max_output_tokens <= 0:
        raise ConfigError(f"{where}: max_output_tokens must be > 0")
    if spec.effort is not None:
        allowed = _EFFORTS.get(spec.provider)
        if allowed is None:
            raise ConfigError(f"{where}: effort is not accepted for {spec.provider} models")
        if spec.effort not in allowed:
            raise ConfigError(f"{where}: effort {spec.effort!r} not in {sorted(allowed)}")
    if spec.thinking is not None:
        if spec.provider != "anthropic":
            raise ConfigError(f"{where}: thinking is accepted only for anthropic models")
        if spec.thinking not in _THINKING:
            raise ConfigError(f"{where}: thinking {spec.thinking!r} not in {sorted(_THINKING)}")
        if spec.thinking != "adaptive" and spec.effort is not None and spec.effort not in _THINKING_OFF_EFFORTS:
            raise ConfigError(f"{where}: thinking {spec.thinking!r} needs effort low, medium or high")


def _validate_doc(doc: _Doc, pricing: dict[str, Any]) -> None:
    for name in doc.providers:
        if name not in PROVIDERS:
            raise ConfigError(f"llm.json providers: unknown provider {name!r} (known: {list(PROVIDERS)})")
    for name, spec in doc.providers.items():
        _finite(spec.timeout_s, f"llm.json providers[{name}].timeout_s", positive=True)
    for name, model_spec in doc.models.items():
        _validate_model(name, model_spec, doc.providers)
    for stage, stage_spec in doc.stage_params.items():
        if stage not in STAGES:
            raise ConfigError(f"llm.json stage_params: unknown stage {stage!r}")
        if stage_spec.max_tokens is not None and stage_spec.max_tokens <= 0:
            raise ConfigError(f"llm.json stage_params[{stage}].max_tokens must be > 0")
        if stage_spec.temperature is not None and not math.isfinite(stage_spec.temperature):
            raise ConfigError(f"llm.json stage_params[{stage}].temperature must be finite")
    for name, mapping in doc.profiles.items():
        missing = [s for s in STAGES if s not in mapping]
        unknown = sorted(set(mapping) - set(STAGES))
        if missing or unknown:
            raise ConfigError(f"llm.json profiles[{name}]: missing stages {missing}, unknown stages {unknown}")
        for stage, model in mapping.items():
            if model not in doc.models:
                raise ConfigError(f"llm.json profiles[{name}].{stage}: model {model!r} is not in models")
            if model not in pricing:
                raise ConfigError(f"pricing.json: no rate row for model {model!r} (profile {name})")
            params = doc.stage_params.get(stage)
            if params is not None and params.temperature is not None and not doc.models[model].temperature:
                raise ConfigError(
                    f"llm.json profiles[{name}].{stage}: {model} takes no temperature, but stage_params sets one")
    retry = doc.retry
    if retry.max_attempts < 1:
        raise ConfigError("llm.json retry.max_attempts must be >= 1")
    _finite(retry.min_wait_s, "llm.json retry.min_wait_s")
    _finite(retry.max_wait_s, "llm.json retry.max_wait_s")
    if retry.max_wait_s < retry.min_wait_s:
        raise ConfigError("llm.json retry.max_wait_s must be >= min_wait_s")
    b = doc.budgets
    if min(b.max_tool_calls_per_phase, b.max_scenarios, b.max_candidates) < 1:
        raise ConfigError("llm.json budgets: counts must be >= 1")
    _finite(b.max_total_cost_usd, "llm.json budgets.max_total_cost_usd", positive=True)


def load_llm_config(
    doc: dict[str, Any],
    pricing: dict[str, Any],
    *,
    profile: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMConfig:
    """Validates ``llm.json`` and ``pricing.json`` contents and resolves the active profile.

    Args:
        doc: The parsed ``llm.json`` document.
        pricing: The parsed ``pricing.json`` document (model name -> rate row).
        profile: An explicit profile name that overrides ``active_profile``.
        environ: Environment for ``${VAR}`` expansion; ``os.environ`` when None.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: The config or pricing is invalid, or the profile is unknown.
    """
    if not isinstance(pricing, dict):
        raise ConfigError("pricing.json must be an object of model -> rate row")
    parsed: _Doc = _validate(_Doc, doc, "llm.json")
    _validate_pricing(pricing)
    _validate_doc(parsed, pricing)
    active = profile if profile is not None else expand_str(parsed.active_profile, environ)
    if active not in parsed.profiles:
        raise ConfigError(f"llm.json: unknown profile {active!r} (known: {sorted(parsed.profiles)})")
    r, b = parsed.retry, parsed.budgets
    return LLMConfig(
        profile=active,
        providers=parsed.providers,
        models=parsed.models,
        stage_models=parsed.profiles[active],
        stage_params=parsed.stage_params,
        retry=RetryPolicy(max_attempts=r.max_attempts, min_wait_s=r.min_wait_s, max_wait_s=r.max_wait_s),
        budgets=Budgets(max_tool_calls_per_phase=b.max_tool_calls_per_phase,
                        max_total_cost_usd=b.max_total_cost_usd, max_scenarios=b.max_scenarios,
                        max_candidates=b.max_candidates),
        pricing=pricing,
    )


def _read_key(env_var: str, environ: Mapping[str, str]) -> str | None:
    """The key from `env_var`, else from the file `<env_var>_FILE` names; None when neither has one."""
    value = (environ.get(env_var) or "").strip()
    if value:
        return value
    path = (environ.get(f"{env_var}_FILE") or "").strip()
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None
    return None


def backend_kwargs(
    config: LLMConfig, provider: str, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """Builds the backend settings for one provider.

    Args:
        config: The loaded configuration.
        provider: The provider name.
        environ: Environment to read the API key from; ``os.environ`` when None.

    Returns:
        ``{"api_key": ..., "base_url": ... or None, "timeout_s": ...}``.

    Raises:
        ConfigError: No non-empty key in the env var or its ``_FILE`` file.
    """
    spec = config.provider_spec(provider)
    key = _read_key(spec.api_key_env, os.environ if environ is None else environ)
    if key is None:
        raise ConfigError(f"{provider}: no API key in {spec.api_key_env} or {spec.api_key_env}_FILE")
    return {"api_key": key, "base_url": spec.base_url, "timeout_s": spec.timeout_s}


def check_api_keys(config: LLMConfig, environ: Mapping[str, str] | None = None) -> None:
    """Checks that every provider the active profile uses has an API key.

    Args:
        config: The loaded configuration.
        environ: Environment to read the API keys from; ``os.environ`` when None.

    Raises:
        ConfigError: Naming every missing key's env var.
    """
    env = os.environ if environ is None else environ
    missing = [
        config.provider_spec(p).api_key_env
        for p in config.providers_in_use()
        if _read_key(config.provider_spec(p).api_key_env, env) is None
    ]
    if missing:
        raise ConfigError(f"profile {config.profile!r}: missing API key(s) {missing} (or their _FILE variants)")


def build_provider_backend(config: LLMConfig, provider: str, environ: Mapping[str, str] | None = None
                           ) -> ChatBackend:
    """Builds the chat backend for one provider.

    Raises:
        ConfigError: The provider has no key, or no backend exists for it.
    """
    kwargs = backend_kwargs(config, provider, environ)
    if provider in ("openai", "deepseek"):  # DeepSeek is OpenAI-compatible (design §5.3)
        return OpenAIChatBackend(api_key=kwargs["api_key"], base_url=kwargs["base_url"],
                                 timeout_s=kwargs["timeout_s"], provider=provider,
                                 models=config.model_options(provider))
    if provider == "anthropic":
        from .anthropic_backend import AnthropicChatBackend

        return AnthropicChatBackend(api_key=kwargs["api_key"], base_url=kwargs["base_url"],
                                    timeout_s=kwargs["timeout_s"], models=config.model_options(provider))
    raise ConfigError(f"no backend for provider {provider!r}")


class RoutingChatBackend:
    """A `ChatBackend` that sends each request to the backend of the requested model's provider."""

    def __init__(self, backends: Mapping[str, ChatBackend], provider_of_model: Mapping[str, str]):
        self._backends = dict(backends)
        self._provider_of_model = dict(provider_of_model)

    def create(self, *, model: str, **kwargs: Any) -> RawCompletion:
        provider = self._provider_of_model.get(model)
        if provider is None or provider not in self._backends:
            raise ConfigError(f"no backend for model {model!r} (provider {provider!r})")
        return self._backends[provider].create(model=model, **kwargs)


def build_chat_backend(config: LLMConfig, environ: Mapping[str, str] | None = None) -> ChatBackend:
    """Builds one backend per provider the active profile uses, routed by model.

    Raises:
        ConfigError: A provider in use has no key or no backend yet.
    """
    backends = {p: build_provider_backend(config, p, environ) for p in config.providers_in_use()}
    routes = {config.model_for(s): config.provider_for(s) for s in STAGES}
    return RoutingChatBackend(backends, routes)
