from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A pip package -> the modules it gives the code. python-multipart is required
# by FastAPI for request.form() but is never imported by name, hence empty.
IMAGE_PACKAGE_MODULES = {
  "fastapi": frozenset({"fastapi", "starlette"}),
  "uvicorn": frozenset({"uvicorn"}),
  "httpx": frozenset({"httpx"}),
  "python-multipart": frozenset(),
}


def test_image_installs_every_third_party_import() -> None:
  imported: set[str] = set()
  for path in sorted((ROOT / "telemulator").rglob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[0] for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.level == 0:
        imported.add((node.module or "").split(".")[0])
  third_party = {
    name
    for name in imported
    if name and name != "telemulator" and name not in sys.stdlib_module_names
  }

  dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
  pip_line = next(line for line in dockerfile.splitlines() if "pip install" in line)
  packages = set(re.findall(r'"([A-Za-z0-9_.\-]+)', pip_line))
  unknown = packages - set(IMAGE_PACKAGE_MODULES)
  assert unknown == set(), f"add to IMAGE_PACKAGE_MODULES: {sorted(unknown)}"

  installed = {module for name in packages for module in IMAGE_PACKAGE_MODULES[name]}
  missing = sorted(third_party - installed)
  assert missing == [], f"missing from the image: {missing} — add them to Dockerfile"
