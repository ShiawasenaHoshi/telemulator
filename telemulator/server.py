from __future__ import annotations

import asyncio

import uvicorn

from telemulator.app import create_app
from telemulator.bot_api import ensure_bot
from telemulator.view import BotView


class TelemulatorServer:
  """Fake Bot API served over loopback so aiogram's real HTTP stack is exercised."""

  def __init__(self) -> None:
    self.app = create_app()
    self._server: uvicorn.Server | None = None
    self._task: asyncio.Task[None] | None = None
    self.port = 0

  @property
  def url(self) -> str:
    return f"http://127.0.0.1:{self.port}"

  def state(self, token: str) -> BotView:
    ensure_bot(self.app.state.network, token)
    return BotView(self.app.state.network, token)

  async def start(self) -> None:
    config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning")
    self._server = uvicorn.Server(config)
    self._task = asyncio.create_task(self._server.serve())
    while not self._server.started:
      await asyncio.sleep(0.01)
    self.port = self._server.servers[0].sockets[0].getsockname()[1]

  async def stop(self) -> None:
    await self.app.state.network.drain_webhooks()
    if self._server is not None:
      self._server.should_exit = True
    if self._task is not None:
      await self._task
