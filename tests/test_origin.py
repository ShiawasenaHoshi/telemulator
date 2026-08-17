from __future__ import annotations

import re
from pathlib import Path

# The emulator is its own product. These words are traces of where it grew up
# and of the project it was extracted from; none of them belong in the code.
STOP_WORDS = ("origin", "tgmock", "club", "payments", "messaging-vendor", "redacted", "org")
STOP_RE = re.compile("|".join(STOP_WORDS), re.IGNORECASE)

# docs/ is an as-is import from a private repository: the history it records is legitimate.
SKIP_DIRS = {".git", ".venv", "docs", "__pycache__", ".pytest_cache", "build", "dist"}
TRACKED_SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".toml", ".md", ".yml", ".yaml"}


def _tracked_files() -> list[Path]:
  root = Path(__file__).resolve().parents[1]
  out: list[Path] = []
  for path in root.rglob("*"):
    if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
      continue
    if path.is_file() and (path.suffix in TRACKED_SUFFIXES or path.name in {"Dockerfile", "Makefile"}):
      out.append(path)
  return out


def test_no_traces_of_the_original_project() -> None:
  offenders: list[str] = []
  for path in _tracked_files():
    if path.name == "test_origin.py":
      continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      if STOP_RE.search(line):
        offenders.append(f"{path}:{number}: {line.strip()}")
  assert offenders == [], "traces of the original project:\n" + "\n".join(offenders)
