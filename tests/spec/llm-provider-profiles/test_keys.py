"""llm-provider-profiles C7: ``backend_kwargs`` and ``check_api_keys`` (Requirement 8).

Providers in the conftest config: openai (OPENAI_API_KEY, 600 s, no base_url), anthropic
(ANTHROPIC_API_KEY, 300 s, no base_url), deepseek (DEEPSEEK_API_KEY, 120 s,
base_url https://api.deepseek.example/v1). ``environ`` is always a dict built in the test; key files
live under pytest's temporary directories.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from src.common.config.models import ConfigError
from src.scenariogen.llm.profiles import backend_kwargs, check_api_keys, load_llm_config

Factory = Callable[[], dict[str, Any]]
ALL_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")


def _cfg(make_doc: Factory, make_pricing: Factory, profile: str) -> Any:
    return load_llm_config(make_doc(), make_pricing(), profile=profile, environ={})


def _key_file(directory: Path, name: str, content: str) -> str:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return str(path)


@pytest.mark.criterion("C7")
def test_c7_key_from_env_var(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 8: key from OPENAI_API_KEY; base_url None (absent); timeout_s 600 as configured.
    cfg = _cfg(make_doc, make_pricing, "openai")
    out = backend_kwargs(cfg, "openai", {"OPENAI_API_KEY": "sk-env-1"})
    assert set(out) == {"api_key", "base_url", "timeout_s"}
    assert out["api_key"] == "sk-env-1"
    assert out["base_url"] is None
    # tolerance: the configured timeout is passed through unchanged.
    assert out["timeout_s"] == pytest.approx(600.0, rel=0, abs=0)


@pytest.mark.criterion("C7")
def test_c7_base_url_and_timeout_as_configured(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 8: deepseek's base_url and timeout_s are read off the conftest config.
    cfg = _cfg(make_doc, make_pricing, "mixed")
    out = backend_kwargs(cfg, "deepseek", {"DEEPSEEK_API_KEY": "ds-1"})
    assert out["api_key"] == "ds-1"
    assert out["base_url"] == "https://api.deepseek.example/v1"
    # tolerance: the configured timeout is passed through unchanged.
    assert out["timeout_s"] == pytest.approx(120.0, rel=0, abs=0)


@pytest.mark.criterion("C7")
def test_c7_file_fallback_when_unset_or_empty(make_doc: Factory, make_pricing: Factory, tmp_path: Path) -> None:
    # oracle: Requirement 8: with the variable unset, or set to "", the key comes from <var>_FILE, with
    # surrounding whitespace stripped ("  sk-file-1\n" -> "sk-file-1"); a set variable wins over the file.
    cfg = _cfg(make_doc, make_pricing, "openai")
    path = _key_file(tmp_path, "openai.key", "  sk-file-1\n")
    assert backend_kwargs(cfg, "openai", {"OPENAI_API_KEY_FILE": path})["api_key"] == "sk-file-1"
    env = {"OPENAI_API_KEY": "", "OPENAI_API_KEY_FILE": path}
    assert backend_kwargs(cfg, "openai", env)["api_key"] == "sk-file-1"
    env = {"OPENAI_API_KEY": "sk-env-2", "OPENAI_API_KEY_FILE": path}
    assert backend_kwargs(cfg, "openai", env)["api_key"] == "sk-env-2"


@pytest.mark.criterion("C7")
def test_c7_missing_key_raises_naming_var(make_doc: Factory, make_pricing: Factory, tmp_path: Path) -> None:
    # oracle: Requirement 8: neither the variable nor a non-empty file -> ConfigError naming ANTHROPIC_API_KEY.
    cfg = _cfg(make_doc, make_pricing, "anthropic")
    blank = _key_file(tmp_path, "blank.key", " \n\t")
    for env in ({}, {"ANTHROPIC_API_KEY": ""}, {"ANTHROPIC_API_KEY_FILE": blank},
                {"OPENAI_API_KEY": "sk-other"}):
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            backend_kwargs(cfg, "anthropic", env)
    assert backend_kwargs(cfg, "anthropic", {"ANTHROPIC_API_KEY": "ak-1"})["api_key"] == "ak-1"


@pytest.mark.criterion("C7")
def test_c7_check_api_keys(make_doc: Factory, make_pricing: Factory) -> None:
    # oracle: Requirement 8: the openai profile uses only openai, so with OPENAI_API_KEY set the check returns
    # None although the other two keys are missing; without it, the error names OPENAI_API_KEY. The mixed
    # profile uses all three, so with only OPENAI_API_KEY set the error names the other two.
    openai_cfg = _cfg(make_doc, make_pricing, "openai")
    assert check_api_keys(openai_cfg, {"OPENAI_API_KEY": "sk-1"}) is None
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        check_api_keys(openai_cfg, {"ANTHROPIC_API_KEY": "ak-1", "DEEPSEEK_API_KEY": "ds-1"})
    mixed = _cfg(make_doc, make_pricing, "mixed")
    with pytest.raises(ConfigError) as info:
        check_api_keys(mixed, {"OPENAI_API_KEY": "sk-1"})
    assert "ANTHROPIC_API_KEY" in str(info.value)
    assert "DEEPSEEK_API_KEY" in str(info.value)


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@pytest.mark.parametrize("key", ["sk-abc", "sk-proj-0123456789", "ds_KEY.with.dots", "x"])
@pytest.mark.parametrize("padding", ["", " ", "\n", "  \t", "\r\n"])
def test_c7_metamorphic_env_and_file_agree(make_doc: Factory, make_pricing: Factory,
                                           tmp_path_factory: pytest.TempPathFactory, key: str,
                                           padding: str) -> None:
    # oracle: Requirement 8: the same key supplied through the variable, or through <var>_FILE padded with
    # whitespace on both sides, gives identical backend settings, and the api_key is the key itself.
    cfg = _cfg(make_doc, make_pricing, "mixed")
    path = _key_file(tmp_path_factory.mktemp("keys"), "deepseek.key", padding + key + padding)
    from_env = backend_kwargs(cfg, "deepseek", {"DEEPSEEK_API_KEY": key})
    from_file = backend_kwargs(cfg, "deepseek", {"DEEPSEEK_API_KEY": "", "DEEPSEEK_API_KEY_FILE": path})
    assert from_env == from_file
    assert from_file["api_key"] == key


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
@pytest.mark.parametrize("profile", ["openai", "anthropic", "mixed"])
@pytest.mark.parametrize("present", [(), ("OPENAI_API_KEY",), ("ANTHROPIC_API_KEY",), ("DEEPSEEK_API_KEY",),
                                     ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"), ALL_VARS])
def test_c7_metamorphic_missing_set(make_doc: Factory, make_pricing: Factory, profile: str,
                                    present: tuple[str, ...]) -> None:
    # oracle: Requirement 8: the missing set is {vars of the providers the profile uses} minus the present
    # ones (openai -> OPENAI; anthropic -> ANTHROPIC; mixed -> all three). Empty -> None; otherwise one
    # ConfigError naming each missing var. Keys for unused providers never change the outcome.
    used = {"openai": {"OPENAI_API_KEY"}, "anthropic": {"ANTHROPIC_API_KEY"}, "mixed": set(ALL_VARS)}[profile]
    missing = used - set(present)
    cfg = _cfg(make_doc, make_pricing, profile)
    environ = {var: "k-" + var.lower() for var in present}
    assert cfg.providers_in_use() == sorted(
        {"OPENAI_API_KEY": "openai", "ANTHROPIC_API_KEY": "anthropic", "DEEPSEEK_API_KEY": "deepseek"}[v]
        for v in used)
    if not missing:
        assert check_api_keys(cfg, environ) is None
        return
    with pytest.raises(ConfigError) as info:
        check_api_keys(cfg, environ)
    for var in missing:
        assert var in str(info.value)
