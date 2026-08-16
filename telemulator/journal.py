from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JournalRecord:
  method: str
  token: str
  kind: str
  params: dict[str, Any] = field(default_factory=dict)
  status: int | None = None
  response: dict[str, Any] | None = None


def journal_item(rec: JournalRecord) -> dict[str, Any]:
  return {
    "method": rec.method,
    "token": rec.token,
    "kind": rec.kind,
    "params": rec.params,
    "status": rec.status,
    "response": rec.response,
  }


CALLS_LIMIT = 500
HOLES_LIMIT = 200


class Journal:
  def __init__(self) -> None:
    self._calls: deque[JournalRecord] = deque(maxlen=CALLS_LIMIT)
    # Separate queue: an emulator hole must not be pushed out by getUpdates noise.
    self._holes: deque[JournalRecord] = deque(maxlen=HOLES_LIMIT)

  def record(
    self,
    method: str,
    token: str,
    kind: str,
    params: dict[str, Any] | None = None,
    status: int | None = None,
    response: dict[str, Any] | None = None,
  ) -> JournalRecord:
    rec = JournalRecord(
      method=method,
      token=token,
      kind=kind,
      params={} if params is None else dict(params),
      status=status,
      response=response,
    )
    self._calls.append(rec)
    if kind != "ok":
      self._holes.append(rec)
    return rec

  def unimplemented(self) -> list[JournalRecord]:
    return [rec for rec in self._holes if rec.kind == "unimplemented"]

  def calls(self) -> list[JournalRecord]:
    return list(self._calls)
