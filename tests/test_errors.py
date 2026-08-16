from __future__ import annotations

from telemulator.errors import BLOCKED, UNAUTHORIZED, bot_error


def test_body_matches_telegram() -> None:
  status, body = bot_error(401, UNAUTHORIZED)
  assert status == 401
  assert body == {"ok": False, "error_code": 401, "description": "Unauthorized"}


def test_429_includes_retry_after() -> None:
  status, body = bot_error(429, "Too Many Requests: retry after 3", retry_after=3)
  assert status == 429
  assert body["parameters"] == {"retry_after": 3}


def test_blocked_description_is_canonical() -> None:
  _, body = bot_error(403, BLOCKED)
  assert body["description"] == "Forbidden: bot was blocked by the user"
