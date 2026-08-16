from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Awaitable

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from telemulator.admin_api import router as admin_router
from telemulator.bot_api import READ_ONLY
from telemulator.bot_api import router as bot_router
from telemulator.limits import limiter_for_profile
from telemulator.network import Network
from telemulator.store import SqliteStore
from telemulator.user_http import router as user_router

_READ_ONLY_PATHS = frozenset({"/admin/journal", "/admin/snapshot"})


def _is_read(path: str) -> bool:
  if path in _READ_ONLY_PATHS:
    return True
  return path.startswith("/bot") and path.rsplit("/", 1)[-1] in READ_ONLY


def _persist(app: FastAPI) -> None:
  store = app.state.store
  if store is None:
    return
  store.save(app.state.network.dump())


# BaseHTTPMiddleware буферизует тело ответа и стопорит SSE при соседнем запросе.
class _PersistAfterMutation:
  def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
    self.app = app

  async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
      await self.app(scope, receive, send)
      return

    status = 0

    async def send_wrapped(message: dict[str, Any]) -> None:
      nonlocal status
      if message["type"] == "http.response.start":
        status = int(message["status"])
      await send(message)
      if message["type"] != "http.response.body" or message.get("more_body", False):
        return
      if (
        scope["method"] != "GET"
        and 200 <= status < 300
        and not _is_read(scope["path"])
      ):
        _persist(scope["app"])

    await self.app(scope, receive, send_wrapped)


def create_app(
  *,
  network: Network | None = None,
  limits_profile: str | None = None,
  sqlite_path: str | None = None,
) -> FastAPI:
  """Fake Telegram Bot API поверх одной сети."""
  app = FastAPI(title="telemulator")
  app.state.network = network or Network()
  app.state.limiter = limiter_for_profile(limits_profile)
  app.state.store = None
  if sqlite_path is not None:
    store = SqliteStore(sqlite_path)
    app.state.store = store
    data = store.load()
    if data is not None:
      app.state.network.load(data)
  app.include_router(bot_router)
  app.include_router(admin_router)
  app.include_router(user_router)
  app.add_middleware(_PersistAfterMutation)

  @app.get("/health")
  async def health() -> dict[str, str]:
    return {"status": "ok"}

  web_dir = Path(__file__).resolve().parent / "web"
  app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
  return app
