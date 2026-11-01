"""PostToolUse hook (Edit|Write|MultiEdit): `ruff check --fix` on the edited .py file.

Never blocks and never reports: it only applies ruff's safe fixes (import order, modern syntax)
to the one file just edited, never removing unused imports and skipping excluded paths. Exits at
once for anything that isn't an existing .py file.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import env_python, project_dir, read_event


def main() -> None:
    """Runs the hook."""
    tool_input = read_event().get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    if not path.endswith(".py") or not os.path.isfile(path):
        return
    py = env_python()
    if py is None:
        return
    # Isolation point: a fixer problem must never block an edit.
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        # Fixed argument list; the path comes from Claude Code's own tool event. No shell.
        # F401 stays unfixable: an import added in one edit is often used only by the next.
        # --force-exclude keeps the configured excludes (scripts/, notebooks/) for explicit paths.
        subprocess.run(  # noqa: S603
            [
                py, "-m", "ruff", "check", "--fix", "--unfixable", "F401", "--force-exclude",
                "--quiet", "--exit-zero", path,
            ],
            cwd=project_dir(),
            capture_output=True,
            timeout=8,
            check=False,
        )


if __name__ == "__main__":
    main()
