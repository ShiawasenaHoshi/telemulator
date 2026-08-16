from __future__ import annotations

from typing import Any

from starlette.requests import Request


def session_request(app: Any, token: str) -> Request:
  """Скоуп ASGI для прямого вызова `events()`.

  httpx ASGITransport ждёт конца стрима, поэтому SSE проверяется вызовом
  хендлера мимо транспорта. Скоуп собран руками — один на все модули, иначе
  очередной обязательный ключ Starlette правится только в одной копии.
  """

  async def receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}

  return Request(
    {
      "type": "http",
      "asgi": {"version": "3.0", "spec_version": "2.4"},
      "http_version": "1.1",
      "method": "GET",
      "scheme": "http",
      "path": "/user/events",
      "raw_path": b"/user/events",
      "query_string": b"",
      "headers": [(b"cookie", f"telemulator_session={token}".encode())],
      "client": ("127.0.0.1", 123),
      "server": ("tg", 80),
      "app": app,
    },
    receive,
  )
