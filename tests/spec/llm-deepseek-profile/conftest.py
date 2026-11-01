"""Offline fakes for the llm-deepseek-profile spec tests.

Fakes sit only at the I/O boundary the spec allows (docs/specs/llm-deepseek-profile.md, Notes): the
``openai`` SDK. ``FakeOpenAIClient`` stands in for ``openai.OpenAI(**kwargs)``; its
``chat.completions.create(**kw)`` records a deep copy of the kwargs and returns a namespace response
naming the requested model, with ``choices[0].message`` (content "ok", no tool calls),
``finish_reason`` "stop" and a ``usage`` built from the fields the test sets.

``fake_openai_module(recorder)`` builds a module object for ``sys.modules["openai"]``; tests install
it with pytest's ``monkeypatch`` (or ``pytest.MonkeyPatch.context()`` inside hypothesis tests).
Everything else (``build_provider_backend``, ``OpenAIChatBackend``, ``InstrumentedChatClient``,
``CostTracker``, ``load_llm_settings``) is the real project code.
"""

from __future__ import annotations

import copy
import sys
import types
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from src.scenariogen.llm.config import load_llm_settings
from src.scenariogen.llm.profiles import LLMConfig

STAGES = ("discover", "evidence", "propose", "calibrate", "narrate", "verify", "attribution")


class Recorder:
    """Shared state of one fake SDK: the recorded requests and the usage the next response carries."""

    def __init__(self, usage: dict[str, Any] | None = None):
        self.requests: list[dict[str, Any]] = []
        self.client_kwargs: list[dict[str, Any]] = []
        self.usage: dict[str, Any] = dict(usage or {"prompt_tokens": 12, "completion_tokens": 3})


class _Completions:
    def __init__(self, recorder: Recorder):
        self._recorder = recorder

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self._recorder.requests.append(copy.deepcopy(kwargs))
        message = SimpleNamespace(content="ok", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(model=kwargs.get("model"), choices=[choice],
                               usage=SimpleNamespace(**self._recorder.usage))


class FakeOpenAIClient:
    """A fake ``openai.OpenAI`` client: accepts any keyword arguments, records every request."""

    def __init__(self, recorder: Recorder | None = None, **kwargs: Any):
        self.recorder = recorder or Recorder()
        self.recorder.client_kwargs.append(dict(kwargs))
        self.chat = SimpleNamespace(completions=_Completions(self.recorder))


def fake_openai_module(recorder: Recorder) -> types.ModuleType:
    """A stand-in ``openai`` module whose ``OpenAI(**kwargs)`` returns a recording fake client."""
    module = types.ModuleType("openai")

    def _openai(**kwargs: Any) -> FakeOpenAIClient:
        return FakeOpenAIClient(recorder, **kwargs)

    module.OpenAI = _openai  # type: ignore[attr-defined]  # fake SDK module built at runtime
    return module


@pytest.fixture(scope="session")
def committed_config() -> Callable[[str], LLMConfig]:
    """Loads the committed llm.json + pricing.json under a profile, with an empty environment."""
    return lambda profile: load_llm_settings(profile=profile, environ={})


@pytest.fixture
def install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> Callable[[Recorder], None]:
    """Installs ``fake_openai_module(recorder)`` as ``sys.modules["openai"]`` for one test."""

    def _install(recorder: Recorder) -> None:
        monkeypatch.setitem(sys.modules, "openai", fake_openai_module(recorder))

    return _install


@pytest.fixture(scope="session")
def sdk() -> SimpleNamespace:
    """The fake-SDK helpers: ``Recorder``, ``FakeOpenAIClient`` and ``fake_openai_module``."""
    return SimpleNamespace(Recorder=Recorder, FakeOpenAIClient=FakeOpenAIClient,
                           fake_openai_module=fake_openai_module)
