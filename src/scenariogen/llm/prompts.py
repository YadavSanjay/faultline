"""Prompt management (P0).

Mirrors ARag's `PromptManager`: prompts live as plain ``.txt`` files under the top-level
``prompts/scenariogen/`` directory, one per (stage, role) pair — e.g. ``propose_system.txt`` +
``propose_user.txt``. Placeholders use ``{var}`` (Python ``str.format``); literal braces in an
embedded JSON example must be doubled (``{{ }}``). Each manager exposes a stable 12-char
signature over the referenced template texts so a generated scenario can be tied back to the
exact prompt source that produced it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Single-brace `{identifier}` not adjacent to another brace (so doubled `{{ }}` literals and
# embedded JSON are left alone). Mirrors ARag's required-variable extraction.
_VAR_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


@dataclass(frozen=True)
class PromptTemplate:
    """A single prompt template with its required `{var}` placeholders."""

    name: str
    text: str
    required_vars: frozenset[str]

    @classmethod
    def from_text(cls, name: str, text: str) -> PromptTemplate:
        return cls(name=name, text=text, required_vars=frozenset(_VAR_RE.findall(text)))

    def render(self, **values: Any) -> str:
        missing = self.required_vars - set(values)
        if missing:
            raise KeyError(f"prompt {self.name!r} missing variables: {sorted(missing)}")
        return self.text.format(**values)


class PromptManager:
    """Loads every ``*.txt`` in a directory into `PromptTemplate`s, addressed by file stem."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._templates: dict[str, PromptTemplate] = {}
        self._load()

    def _load(self) -> None:
        if not self.directory.exists():
            raise FileNotFoundError(f"prompt directory not found: {self.directory}")
        for path in sorted(self.directory.glob("*.txt")):
            self._templates[path.stem] = PromptTemplate.from_text(
                path.stem, path.read_text(encoding="utf-8")
            )

    def get(self, name: str) -> PromptTemplate:
        if name not in self._templates:
            raise KeyError(f"prompt {name!r} not found in {self.directory} ({sorted(self._templates)})")
        return self._templates[name]

    def render(self, name: str, **values: Any) -> str:
        return self.get(name).render(**values)

    def names(self) -> list[str]:
        return sorted(self._templates)

    def signature(self) -> str:
        """Stable 12-char content hash over all template texts (order-independent)."""
        digest = hashlib.sha256()
        for name in sorted(self._templates):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self._templates[name].text.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()[:12]
