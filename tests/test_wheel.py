from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Everything the build backend reads. Copying exactly this — rather than
# building from ROOT — keeps the build off the source tree, where setuptools
# would drop build/ and telemulator.egg-info/ and two xdist workers running
# this module would race each other over them.
BUILD_INPUTS = ("pyproject.toml", "README.md", "LICENSE")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
  work = tmp_path_factory.mktemp("wheelsrc")
  for name in BUILD_INPUTS:
    shutil.copy2(ROOT / name, work / name)
  shutil.copytree(
    ROOT / "telemulator",
    work / "telemulator",
    ignore=shutil.ignore_patterns("__pycache__"),
  )
  out = work / "dist"
  subprocess.run(
    [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(work)],
    check=True,
    capture_output=True,
  )
  built = list(out.glob("telemulator-*.whl"))
  assert built, "pip wheel produced nothing"
  return built[0]


def test_wheel_carries_the_method_catalog(wheel: Path) -> None:
  with zipfile.ZipFile(wheel) as archive:
    assert "telemulator/catalog.json" in archive.namelist()


def test_wheel_carries_the_web_client(wheel: Path) -> None:
  with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
  for asset in ("index.html", "style.css", "app.js"):
    assert f"telemulator/web/{asset}" in names, asset
