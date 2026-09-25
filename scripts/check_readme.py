"""Execute every fenced code block in a Markdown file.

Rules:
- ``python`` blocks run in order in one shared namespace, so later blocks may reuse earlier names.
- A ``text`` block must follow a ``python`` block and must equal that block's standard output.
- ``sh`` blocks run with ``bash -euo pipefail`` from the repository root, with a throwaway
  virtual environment first on ``PATH`` so ``pip install`` never touches the caller's environment.
- Install URLs that pin this repository (``git+<Repository URL>@v<version>``) must name the
  version in ``pyproject.toml`` and are redirected to the local checkout, so the check installs
  the code under review instead of a tag that a release pull request has not created yet.
- ``<!-- same-as: path -->`` directly above a block asserts the block equals that file.
- Any other block language fails, so nothing in the README goes unverified.

Usage: python scripts/check_readme.py README.md
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

FENCE = re.compile(r"^```(\w*)\s*$")
SAME_AS = re.compile(r"^<!-- same-as: (\S+) -->$")
ROOT = Path(__file__).resolve().parent.parent


def blocks(markdown: str) -> list[tuple[int, str, str, str | None]]:
    """Return (line, language, body, same_as) for every fenced block."""
    found: list[tuple[int, str, str, str | None]] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        match = FENCE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        previous = lines[start - 1] if start else ""
        same_as = SAME_AS.match(previous)
        end = start + 1
        while end < len(lines) and lines[end].strip() != "```":
            end += 1
        if end == len(lines):
            raise SystemExit(f"line {start + 1}: unterminated code block")
        body = "\n".join(lines[start + 1 : end]) + "\n"
        found.append((start + 1, match.group(1), body, same_as and same_as.group(1)))
        index = end + 1
    return found


def pin_to_checkout(body: str, label: str) -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pinned = re.compile(re.escape(f"git+{project['urls']['Repository']}") + r"@v([\w.]+)")
    for version in pinned.findall(body):
        if version != project["version"]:
            raise SystemExit(f"{label}: pins v{version} but pyproject.toml is {project['version']}")
    return pinned.sub(ROOT.as_uri(), body)


def run_shell(body: str, env: dict[str, str]) -> None:
    subprocess.run(["bash", "-euo", "pipefail", "-c", body], cwd=ROOT, env=env, check=True)


def main(path: str) -> int:
    namespace: dict[str, object] = {"__name__": "__main__"}
    last_output: str | None = None
    with tempfile.TemporaryDirectory(prefix="readme-check-") as tmp:
        venv.create(tmp, with_pip=True)
        env = {**os.environ, "PATH": f"{Path(tmp) / 'bin'}{os.pathsep}{os.environ['PATH']}"}
        env.pop("VIRTUAL_ENV", None)
        for line, language, body, same_as in blocks(Path(path).read_text()):
            label = f"{path}:{line} ({language or 'no language'})"
            if same_as is not None and (ROOT / same_as).read_text() != body:
                raise SystemExit(f"{label}: differs from {same_as}")
            if language == "python":
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    exec(compile(body, label, "exec"), namespace)  # noqa: S102
                last_output = buffer.getvalue()
            elif language == "text":
                if last_output is None:
                    raise SystemExit(f"{label}: text block has no preceding python block")
                if last_output.strip() != body.strip():
                    raise SystemExit(f"{label}: expected\n{body}\ngot\n{last_output}")
                last_output = None
            elif language == "sh":
                run_shell(pin_to_checkout(body, label), env)
            else:
                raise SystemExit(f"{label}: unsupported block language")
            print(f"ok {label}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "README.md"))
