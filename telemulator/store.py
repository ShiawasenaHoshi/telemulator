from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

_NETWORK_KEY = "network"


class MemoryStore:
  def __init__(self) -> None:
    self._data: dict[str, Any] | None = None

  def load(self) -> dict[str, Any] | None:
    return self._data

  def save(self, data: dict[str, Any]) -> None:
    self._data = data


class SqliteStore:
  def __init__(self, path: str | Path) -> None:
    self._path = str(path)

  def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(self._path)
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    return conn

  def load(self) -> dict[str, Any] | None:
    if not Path(self._path).exists():
      return None
    with self._connect() as conn:
      row = conn.execute("SELECT v FROM kv WHERE k = ?", (_NETWORK_KEY,)).fetchone()
    if row is None:
      return None
    return json.loads(row[0])

  def save(self, data: dict[str, Any]) -> None:
    blob = json.dumps(data)
    with self._connect() as conn:
      conn.execute(
        "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
        (_NETWORK_KEY, blob),
      )
