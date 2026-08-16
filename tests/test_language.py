from __future__ import annotations

import re
from pathlib import Path

CYRILLIC = re.compile(r"[\u0400-\u04FF]")

# docs/ is an as-is import from a private repository and stays in Russian on purpose.
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


def test_the_product_speaks_english() -> None:
  offenders: list[str] = []
  for path in _tracked_files():
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      if CYRILLIC.search(line):
        offenders.append(f"{path}:{number}: {line.strip()}")
  assert offenders == [], "Cyrillic outside docs/:\n" + "\n".join(offenders)
