from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable

# Bots FAQ as of 2026-08: https://core.telegram.org/bots/faq#broadcasting-to-users
PER_CHAT_PER_SEC = 1.0
GLOBAL_PER_SEC = 30.0
GROUP_PER_MIN = 20.0

TELEGRAM_FAQ = "telegram-faq"
_WINDOW_SEC = 1.0
_GROUP_WINDOW_SEC = 60.0


class RateLimiter:
  def __init__(
    self,
    clock: Callable[[], float] = time.monotonic,
    *,
    per_chat_per_sec: float = PER_CHAT_PER_SEC,
    global_per_sec: float = GLOBAL_PER_SEC,
  ) -> None:
    self._clock = clock
    self._per_chat_per_sec = per_chat_per_sec
    self._global_per_sec = global_per_sec
    self._per_chat: dict[tuple[int, int], list[float]] = defaultdict(list)
    self._global: dict[int, list[float]] = defaultdict(list)
    self._group: dict[tuple[int, int], list[float]] = defaultdict(list)

  def check(self, bot_id: int, chat_id: int, *, group_chat: bool = False) -> int | None:
    now = self._clock()
    chat_wait = _retry_after(self._per_chat[(bot_id, chat_id)], now, self._per_chat_per_sec)
    if chat_wait is not None:
      return chat_wait
    glob = _retry_after(self._global[bot_id], now, self._global_per_sec)
    if glob is not None:
      return glob
    if group_chat:
      return _retry_after(
        self._group[(bot_id, chat_id)], now, GROUP_PER_MIN, _GROUP_WINDOW_SEC
      )
    return None

  def record(self, bot_id: int, chat_id: int, *, group_chat: bool = False) -> None:
    now = self._clock()
    self._per_chat[(bot_id, chat_id)].append(now)
    self._global[bot_id].append(now)
    if group_chat:
      self._group[(bot_id, chat_id)].append(now)


def limiter_for_profile(profile: str | None) -> RateLimiter | None:
  if profile is None:
    return None
  if profile != TELEGRAM_FAQ:
    raise ValueError(f"unknown limits profile: {profile}")
  return RateLimiter()


def _retry_after(
  stamps: list[float], now: float, limit: float, window_sec: float = _WINDOW_SEC
) -> int | None:
  cutoff = now - window_sec
  stamps[:] = [stamp for stamp in stamps if stamp > cutoff]
  if len(stamps) < limit:
    return None
  oldest = stamps[0]
  wait = window_sec - (now - oldest)
  if wait <= 0:
    return None
  return math.ceil(wait)
