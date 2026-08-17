from __future__ import annotations

import re

from repo_files import in_docs, tracked_text_files

# Escaped, not a literal character class: written literally, this line would
# be the guard's own first offender.
CYRILLIC = re.compile(r"[\u0400-\u04FF]")


def test_the_product_speaks_english() -> None:
  """docs/ is an as-is import of design history and stays in Russian."""
  offenders: list[str] = []
  for path in tracked_text_files():
    if in_docs(path):
      continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      if CYRILLIC.search(line):
        offenders.append(f"{path}:{number}: {line.strip()}")
  assert offenders == [], "Cyrillic outside docs/:\n" + "\n".join(offenders)
