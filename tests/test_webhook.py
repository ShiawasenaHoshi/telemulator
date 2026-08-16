from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from uvicorn import Config, Server
import asyncio
import pytest

from telemulator import create_app
from telemulator.errors import CONFLICT_GETUPDATES

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_webhook_disables_get_updates_and_posts_json() -> None:
  received: list[dict] = []
  hook = FastAPI()

  @hook.post("/hook")
  async def catch(request: Request) -> dict:
    received.append(await request.json())
    return {"ok": True}

  cfg = Config(hook, host="127.0.0.1", port=0, log_level="warning")
  server = Server(cfg)
  task = asyncio.create_task(server.serve())
  while not server.started:
    await asyncio.sleep(0.01)
  port = server.servers[0].sockets[0].getsockname()[1]
  url = f"http://127.0.0.1:{port}/hook"
  try:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
      await client.post("/admin/bots", json={"token": TOKEN})
      await client.post(f"/bot{TOKEN}/setWebhook", data={"url": url})
      conflict = await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
      assert conflict.status_code == 409
      app.state.network.push_update(TOKEN, {"message": {"text": "hi"}})
      await asyncio.sleep(0.2)
      assert received[0]["message"]["text"] == "hi"
      info = (await client.post(f"/bot{TOKEN}/getWebhookInfo")).json()["result"]
      assert info["url"] == url
  finally:
    server.should_exit = True
    await task


async def test_second_get_updates_cancels_the_first_with_409() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    first = asyncio.create_task(
      client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "2"})
    )
    await asyncio.sleep(0.1)
    second = await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "2"})
    first_resp = await first
    assert first_resp.status_code == 409
    assert first_resp.json()["description"] == CONFLICT_GETUPDATES
    assert second.status_code == 200
    assert second.json()["ok"] is True


async def test_webhook_non_2xx_records_last_error() -> None:
  hook = FastAPI()

  @hook.post("/hook")
  async def fail() -> JSONResponse:
    return JSONResponse({"ok": False}, status_code=500)

  cfg = Config(hook, host="127.0.0.1", port=0, log_level="warning")
  server = Server(cfg)
  task = asyncio.create_task(server.serve())
  while not server.started:
    await asyncio.sleep(0.01)
  port = server.servers[0].sockets[0].getsockname()[1]
  url = f"http://127.0.0.1:{port}/hook"
  try:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
      await client.post("/admin/bots", json={"token": TOKEN})
      await client.post(f"/bot{TOKEN}/setWebhook", data={"url": url})
      app.state.network.push_update(TOKEN, {"message": {"text": "hi"}})
      await asyncio.sleep(0.2)
      info = (await client.post(f"/bot{TOKEN}/getWebhookInfo")).json()["result"]
      assert info["last_error_message"]
      assert info["last_error_date"]
      queued = await app.state.network.take_updates(TOKEN, None, 0.0)
      assert queued == []
  finally:
    server.should_exit = True
    await task


async def test_client_cancel_is_not_a_conflict() -> None:
  """Cancelling the request is not a 409: otherwise stopping the server turns into a conflict."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    poll = asyncio.create_task(client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "2"}))
    await asyncio.sleep(0.1)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
      await poll


async def test_drain_webhooks_leaves_nothing_pending() -> None:
  app = create_app()
  net = app.state.network
  net.create_bot(token=TOKEN)
  net.bots[TOKEN].webhook_url = "http://127.0.0.1:1/hook"  # no connection — fails fast
  net.push_update(TOKEN, {"message": {"text": "hi"}})
  await net.drain_webhooks()
  assert net._webhook_tasks == set()
