"""LLM scenario-artifact persistence (P0).

Each generation run writes its artifacts under ``data/pricer/scenario_llm/<run_id>/`` — the run
manifest, proposals, evidence, calibrated definitions, per-scenario narratives (markdown), and
the cost rollup. Writes are atomic (temp + ``os.replace``), mirroring
``src/scenariogen/report.py``. Calibrated scenarios additionally flow into the existing
`ScenarioStore` + `StressReport` path (wired in P2/P4); this store keeps the LLM-specific
provenance the standard stores don't model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.common import storage

from .schema import LLMScenarioArtifact


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_to_jsonable(item) for item in obj]
    return obj


def _ret(loc: str) -> Path | str:
    return loc if storage.is_remote_uri(loc) else Path(loc)


class LLMScenarioArtifactStore:
    """Reads/writes the per-run artifact tree under data/pricer/scenario_llm/."""

    def __init__(self, project_root: Path):
        self.root = storage.join(storage.data_base(project_root), "pricer", "scenario_llm")

    def run_dir(self, run_id: str) -> Path | str:
        return _ret(storage.join(self.root, run_id))

    def write_json(self, run_id: str, filename: str, obj: Any) -> Path | str:
        path = storage.join(self.root, run_id, filename)
        storage.write_text_atomic(path, json.dumps(_to_jsonable(obj), indent=2, default=str))
        return _ret(path)

    def write_text(self, run_id: str, relpath: str, text: str) -> Path | str:
        path = storage.join(self.root, run_id, relpath)
        storage.write_text_atomic(path, text)
        return _ret(path)

    def write_manifest(self, artifact: LLMScenarioArtifact) -> Path | str:
        return self.write_json(artifact.run_id, "manifest.json", artifact)

    def write_narrative(self, run_id: str, scenario_name: str, markdown: str) -> Path | str:
        return self.write_text(run_id, f"narratives/{scenario_name}.md", markdown)

    def read_manifest(self, run_id: str) -> LLMScenarioArtifact:
        path = storage.join(self.root, run_id, "manifest.json")
        return LLMScenarioArtifact.model_validate_json(storage.read_text(path))

    def list_runs(self) -> list[str]:
        if storage.is_remote_uri(self.root):
            prefix = self.root.rstrip("/") + "/"
            ids = {
                loc.replace("\\", "/")[len(prefix):].split("/", 1)[0]
                for loc in storage.glob(self.root, "*/manifest.json")
            }
            return sorted(ids)
        root = Path(self.root)
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir())
