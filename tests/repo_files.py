from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".toml", ".md", ".yml", ".yaml"}
TEXT_NAMES = {"Dockerfile", "Makefile"}


def tracked_text_files() -> list[Path]:
  """Every text file git would publish, and nothing else.

  Asking git rather than walking the tree is the point: the guards protect
  what leaves this repository, so untracked scratch — virtualenvs, build
  output, a worktree someone parked under .claude/ — must not be able to fail
  them, and an ignored file must not be able to hide from them either.
  """
  listed = subprocess.run(
    ["git", "-C", str(ROOT), "ls-files", "-z"],
    check=True,
    capture_output=True,
    text=True,
  ).stdout
  out: list[Path] = []
  for name in listed.split("\0"):
    if not name:
      continue
    path = ROOT / name
    if path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES:
      out.append(path)
  return out


def in_docs(path: Path) -> bool:
  return "docs" in path.relative_to(ROOT).parts
