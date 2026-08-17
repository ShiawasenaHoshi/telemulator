from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
  out = tmp_path_factory.mktemp("wheel")
  subprocess.run(
    [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(ROOT)],
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
