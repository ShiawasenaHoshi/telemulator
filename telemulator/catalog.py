from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

_PATH = Path(__file__).with_name("catalog.json")
_cached: frozenset[str] | None = None

Kind = Literal["ok", "unimplemented", "unknown"]


def _raw() -> dict[str, Any]:
  return json.loads(_PATH.read_text(encoding="utf-8"))


def catalog_version() -> str:
  return str(_raw()["bot_api"])


def load_catalog() -> frozenset[str]:
  global _cached
  if _cached is None:
    _cached = frozenset(_raw()["methods"])
  return _cached


def classify_method(name: str, implemented: frozenset[str]) -> Kind:
  if name in implemented:
    return "ok"
  if name in load_catalog():
    return "unimplemented"
  return "unknown"
