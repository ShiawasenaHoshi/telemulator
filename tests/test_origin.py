from __future__ import annotations

import base64
import re
from pathlib import Path

# Names that must never appear anywhere in this repo, including docs/: the
# private project this emulator was extracted from, its vendors, and a
# person's name. Base64-encoded so this guard doesn't itself republish the
# names it exists to keep out.
_IDENTITY_WORDS_B64 = (
    "bmF0a3U=",
    "d2F5Zm9ycGF5",
    "c21hcnRzZW5kZXI=",
    "0YbQuC3QutC70YPQsQ==",
    "0LrRg9C90LjQvQ==",
)
IDENTITY_WORDS = tuple(base64.b64decode(w).decode("utf-8") for w in _IDENTITY_WORDS_B64)
IDENTITY_RE = re.compile("|".join(re.escape(w) for w in IDENTITY_WORDS), re.IGNORECASE)

# The emulator's own former working name, and a generic word tied to the
# origin project's domain. Fine in docs/ as historical record; must not creep
# into the shipped product.
CODE_WORDS = ("tgmock", "club")
CODE_RE = re.compile("|".join(re.escape(w) for w in CODE_WORDS), re.IGNORECASE)

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist"}
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
    in_docs = "docs" in path.relative_to(Path(__file__).resolve().parents[1]).parts
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      if IDENTITY_RE.search(line) or (not in_docs and CODE_RE.search(line)):
        offenders.append(f"{path}:{number}: {line.strip()}")
  assert offenders == [], "traces of the original project:\n" + "\n".join(offenders)
