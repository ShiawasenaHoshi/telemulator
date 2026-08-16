from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from uvicorn import Config, Server

from telemulator import create_app
from telemulator.network import Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


async def test_default_hides_chat_member_keeps_my_chat_member() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/bots", json={"token": ALERT})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(
      chat["id"], 222222222, actor_id=1, status="administrator",
      flags={"can_restrict_members": True},
    )
    net.add_member(chat["id"], 111111111, actor_id=1)
    mine = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    assert any("my_chat_member" in u for u in mine)
    other = (
      await client.post(f"/bot{ALERT}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    assert not any("chat_member" in u for u in other)


async def test_omitted_keeps_explicit_empty_resets_on_getupdates_and_webhook() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post(
      f"/bot{TOKEN}/getUpdates",
      data={"timeout": "0", "allowed_updates": json.dumps(["callback_query"])},
    )
    net = app.state.network
    net.push_update(TOKEN, {"message": {"text": "nope"}})
    net.push_update(TOKEN, {"callback_query": {"id": "cb-1", "data": "x"}})
    kept = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    kinds = [next(k for k in u if k != "update_id") for u in kept]
    assert "callback_query" in kinds
    assert "message" not in kinds
    await client.post(
      f"/bot{TOKEN}/getUpdates",
      data={"timeout": "0", "allowed_updates": json.dumps([])},
    )
    net.push_update(TOKEN, {"message": {"text": "again"}})
    after = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    assert any(u.get("message", {}).get("text") == "again" for u in after)
    await client.post(
      f"/bot{TOKEN}/setWebhook",
      data={"url": "http://127.0.0.1/hook", "allowed_updates": json.dumps(["message"])},
    )
    info = (await client.post(f"/bot{TOKEN}/getWebhookInfo")).json()["result"]
    assert info["allowed_updates"] == ["message"]
    await client.post(f"/bot{TOKEN}/setWebhook", data={"url": "http://127.0.0.1/hook"})
    info2 = (await client.post(f"/bot{TOKEN}/getWebhookInfo")).json()["result"]
    assert info2["allowed_updates"] == ["message"]
    await client.post(
      f"/bot{TOKEN}/setWebhook",
      data={"url": "", "allowed_updates": json.dumps([])},
    )
    info3 = (await client.post(f"/bot{TOKEN}/getWebhookInfo")).json()["result"]
    assert "allowed_updates" not in info3
    snap = (await client.post("/admin/snapshot")).json()
    bot = next(b for b in snap["bots"] if b["token"] == TOKEN)
    assert "allowed_updates" not in bot or bot.get("allowed_updates") is None


async def test_subscription_change_does_not_drop_already_queued() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    app.state.network.push_update(TOKEN, {"message": {"text": "old"}})
    first = (
      await client.post(
        f"/bot{TOKEN}/getUpdates",
        data={"timeout": "0", "allowed_updates": json.dumps(["callback_query"])},
      )
    ).json()["result"]
    assert any(u.get("message", {}).get("text") == "old" for u in first)


async def test_same_subscription_twice_keeps_queued_updates_hidden() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    poll = {"timeout": "0", "allowed_updates": json.dumps(["callback_query"])}
    await client.post(f"/bot{TOKEN}/getUpdates", data=poll)
    net = app.state.network
    cutoff = net.bots[TOKEN].allowed_updates_cutoff
    net.push_update(TOKEN, {"message": {"text": "nope"}})
    net.push_update(TOKEN, {"chat_member": {"x": 1}})
    again = (await client.post(f"/bot{TOKEN}/getUpdates", data=poll)).json()["result"]
    assert net.bots[TOKEN].allowed_updates_cutoff == cutoff
    assert not any("message" in u or "chat_member" in u for u in again)


async def test_setwebhook_with_same_list_keeps_queued_updates_hidden() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    # empty url: setWebhook writes the subscription, but updates stay in the queue.
    hook = {"url": "", "allowed_updates": json.dumps(["callback_query"])}
    await client.post(f"/bot{TOKEN}/setWebhook", data=hook)
    net = app.state.network
    net.push_update(TOKEN, {"message": {"text": "nope"}})
    await client.post(f"/bot{TOKEN}/setWebhook", data=hook)
    assert net.bots[TOKEN].allowed_updates == ["callback_query"]
    kept = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    assert not any("message" in u for u in kept)


async def test_long_poll_waits_for_allowed_type() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post(
      f"/bot{TOKEN}/getUpdates",
      data={"timeout": "0", "allowed_updates": json.dumps(["callback_query"])},
    )
    app.state.network.push_update(TOKEN, {"message": {"text": "nope"}})
    pending = asyncio.create_task(
      client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "2"})
    )
    await asyncio.sleep(0.15)
    assert not pending.done()
    app.state.network.push_update(TOKEN, {"callback_query": {"id": "cb-2", "data": "y"}})
    resp = await pending
    kinds = [next(k for k in u if k != "update_id") for u in resp.json()["result"]]
    assert kinds == ["callback_query"]


def test_dump_has_allowed_updates_only_after_explicit_list() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  bot = next(b for b in net.dump()["bots"] if b["token"] == TOKEN)
  assert "allowed_updates" not in bot
  assert bot["allowed_updates_cutoff"] == 0
  net.bots[TOKEN].allowed_updates = ["message"]
  net.bots[TOKEN].allowed_updates_cutoff = 4
  dumped = next(b for b in net.dump()["bots"] if b["token"] == TOKEN)
  assert dumped["allowed_updates"] == ["message"]
  assert dumped["allowed_updates_cutoff"] == 4
  restored = Network()
  restored.load(net.dump())
  assert restored.bots[TOKEN].allowed_updates == ["message"]
  assert restored.bots[TOKEN].allowed_updates_cutoff == 4
  old = Network()
  old.load({"bots": [{"token": TOKEN, "user": {"id": 1, "is_bot": True, "first_name": "B"}}]})
  assert old.bots[TOKEN].allowed_updates is None
  assert old.bots[TOKEN].allowed_updates_cutoff == 0


def test_reset_copies_allowed_updates() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  net.bots[TOKEN].allowed_updates = ["callback_query"]
  net.bots[TOKEN].allowed_updates_cutoff = 7
  fresh = net.reset()
  assert fresh.bots[TOKEN].allowed_updates == ["callback_query"]
  assert fresh.bots[TOKEN].allowed_updates_cutoff == 7


async def test_webhook_does_not_post_filtered_types() -> None:
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
      await client.post(
        f"/bot{TOKEN}/setWebhook",
        data={"url": url, "allowed_updates": json.dumps(["callback_query"])},
      )
      app.state.network.push_update(TOKEN, {"message": {"text": "nope"}})
      app.state.network.push_update(TOKEN, {"callback_query": {"id": "cb-1", "data": "x"}})
      await app.state.network.drain_webhooks()
      kinds = [next(k for k in u if k != "update_id") for u in received]
      assert kinds == ["callback_query"]
  finally:
    server.should_exit = True
    await task
