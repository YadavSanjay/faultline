"""Service-location config resolves through the loaders, env-overridable in one place (F6).

Confirms the real configs/scenariogen/{arag,macro}.json + llm/mcp.json placeholders resolve to
the local-dev defaults when the environment is unset, and to the override when the single env
var is set — so "moving ARag" is one environment edit, not a hunt across JSON files.
"""

from __future__ import annotations

from src.scenariogen.config import load_arag_config, load_macro_config
from src.scenariogen.llm.config import load_mcp_settings
from src.scenariogen.llm.mcp.client import parse_server_specs


def test_arag_base_url_default_and_override(monkeypatch):
    monkeypatch.delenv("ARAG_BASE_URL", raising=False)
    assert load_arag_config()["connection"]["base_url"] == "http://localhost:8001"

    monkeypatch.setenv("ARAG_BASE_URL", "http://arag.prod:9000")
    assert load_arag_config()["connection"]["base_url"] == "http://arag.prod:9000"


def test_macro_base_url_override(monkeypatch):
    monkeypatch.setenv("MACRORAG_BASE_URL", "http://macro.prod:9100")
    assert load_macro_config()["connection"]["base_url"] == "http://macro.prod:9100"


def test_mcp_arag_location_default_and_override(monkeypatch):
    monkeypatch.delenv("ARAG_HOME", raising=False)
    arag = load_mcp_settings()["servers"]["arag"]
    assert arag["cwd"] == "../XbrlRag"
    assert arag["env"]["PYTHONPATH"] == "../XbrlRag/src"

    monkeypatch.setenv("ARAG_HOME", "/opt/arag")
    arag2 = load_mcp_settings()["servers"]["arag"]
    assert arag2["cwd"] == "/opt/arag"
    assert arag2["env"]["PYTHONPATH"] == "/opt/arag/src"       # suffix preserved


def test_mcp_macro_location_default_and_override(monkeypatch):
    monkeypatch.delenv("MACRORAG_HOME", raising=False)
    macro = load_mcp_settings()["servers"]["macro"]
    assert macro["cwd"] == "../MacroRag"          # the public repo's name, as a clone creates it
    assert macro["env"]["PYTHONPATH"] == "../MacroRag/src"

    monkeypatch.setenv("MACRORAG_HOME", "/opt/macro")
    macro2 = load_mcp_settings()["servers"]["macro"]
    assert macro2["cwd"] == "/opt/macro"
    assert macro2["env"]["PYTHONPATH"] == "/opt/macro/src"


def test_mcp_raginfer_url_shared_override(monkeypatch):
    monkeypatch.setenv("RAGINFER_URL", "http://infer.prod:8799")
    servers = load_mcp_settings()["servers"]
    assert servers["arag"]["env"]["RAGINFER_URL"] == "http://infer.prod:8799"
    assert servers["macro"]["env"]["RAGINFER_URL"] == "http://infer.prod:8799"


def test_server_specs_are_exactly_the_two_real_servers():
    # Guard: a doc/_env_note key must live at the top level, never inside "servers"
    # (else parse_server_specs would try to build a spec from it and crash).
    assert set(parse_server_specs(load_mcp_settings())) == {"arag", "macro"}
