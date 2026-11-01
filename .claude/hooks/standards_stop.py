"""Stop hook: runs `tools/standards/check_standards.py stop --hook` in the py312 env.

The checker decides everything (skip when nothing relevant changed, advisory vs blocking,
`stop_hook_active`); this wrapper only passes the event through and fails open with a warning
when the env is missing or the check times out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _hooklib import env_python, project_dir

_TIMEOUT_S = 50  # below the 60 s hook timeout in .claude/settings.json


def main() -> None:
    """Runs the hook."""
    event = sys.stdin.read()
    py = env_python()
    if py is None:
        sys.stdout.write(json.dumps({"systemMessage": "standards: py312 env not found; Stop check skipped."}))
        return
    root = project_dir()
    try:
        # Fixed argument list for the repo's own checker; no shell.
        proc = subprocess.run(  # noqa: S603
            [py, os.path.join(root, "tools", "standards", "check_standards.py"), "--root", root, "stop", "--hook"],
            input=event,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_S,
            check=False,
            cwd=root,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stdout.write(json.dumps({"systemMessage": f"standards: Stop check did not finish ({exc}); skipped."}))
        return
    sys.stdout.write(proc.stdout)


if __name__ == "__main__":
    main()
