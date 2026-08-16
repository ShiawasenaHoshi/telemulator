from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from asgi import session_request
from telemulator import create_app
from telemulator.errors import CHAT_NOT_FOUND
from telemulator.user_http import events

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_admin_creates_dialog_so_bot_can_write() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    user = (await client.post("/admin/users", json={"id": 42, "first_name": "Anna"})).json()["user"]
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 42, "bot_token": TOKEN})
    sent = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "hi"})
    assert sent.status_code == 200
    assert user["id"] == 42


async def test_snapshot_roundtrip() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 7, "first_name": "X"})
    snap = (await client.post("/admin/snapshot")).json()
    await client.post("/admin/reset")
    missing = await client.post("/admin/dialogs", json={"user_id": 7, "bot_token": TOKEN})
    assert missing.status_code == 400
    await client.post("/admin/snapshot/restore", json=snap)
    user = app.state.network.users[7]
    assert user["first_name"] == "X"


async def test_journal_lists_unimplemented() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post(f"/bot{TOKEN}/sendPoll", data={"chat_id": "1"})
    journal = (await client.get("/admin/journal")).json()
    assert journal["unimplemented"][0]["method"] == "sendPoll"


async def test_journal_includes_bot_api_response() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 42, "first_name": "Anna"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 42, "bot_token": TOKEN})
    await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "hi"})
    journal = (await client.get("/admin/journal")).json()
    sent = [c for c in journal["calls"] if c["method"] == "sendMessage"][-1]
    assert sent["status"] == 200
    assert sent["response"]["ok"] is True
    assert sent["response"]["result"]["text"] == "hi"
    assert sent["params"]["text"] == "hi"


async def test_cancelled_get_updates_is_still_journaled() -> None:
  """The bot left mid long-poll — the call is still visible in the journal."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    poll = asyncio.create_task(
      client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "5"})
    )
    await asyncio.sleep(0.1)
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
      await poll

    journal = (await client.get("/admin/journal")).json()
    polled = [c for c in journal["calls"] if c["method"] == "getUpdates"]
    assert polled and polled[-1]["status"] == 499


async def test_get_updates_journal_keeps_only_result_count() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post(f"/admin/{TOKEN}/updates", json={"chat_id": 42, "text": "hi"})
    await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    journal = (await client.get("/admin/journal")).json()
    polled = [c for c in journal["calls"] if c["method"] == "getUpdates"][-1]
    assert polled["response"] == {"ok": True, "result_count": 1}


async def test_bot_api_emits_journal_event() -> None:
  app = create_app()
  queue = app.state.network.subscribe()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post(f"/bot{TOKEN}/getMe")
  event = queue.get_nowait()
  assert event["type"] == "journal"
  assert event["record"]["method"] == "getMe"
  assert event["record"]["status"] == 200


async def test_reset_keeps_update_ids_monotonic_for_a_polling_bot() -> None:
  """A bot in compose survives reset and remembers its offset — defect #33."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post(f"/admin/{TOKEN}/updates", json={"chat_id": 900900, "text": "/start"})
    before = (await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})).json()
    offset = before["result"][-1]["update_id"] + 1

    await client.post("/admin/reset")
    await client.post(f"/admin/{TOKEN}/updates", json={"chat_id": 900900, "text": "/start"})

    after = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0", "offset": str(offset)})
    ).json()["result"]
    assert [u["message"]["text"] for u in after] == ["/start"]


async def test_reset_forgets_users_and_chats() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 42, "first_name": "Anna"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 42, "bot_token": TOKEN})
    await client.post("/admin/reset")
    blocked = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "hi"})
    assert blocked.status_code == 400
    assert blocked.json()["description"] == CHAT_NOT_FOUND


async def test_reset_keeps_the_sse_pipe_and_drops_the_session() -> None:
  """The tab survives reset: the connection stays alive, we create the session again."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    queue = app.state.network.subscribe()

    await client.post("/admin/reset")

    assert queue in app.state.network._subscribers
    assert queue.get_nowait() == {"type": "reset"}
    assert (await client.get("/user/me")).status_code == 401


async def test_reset_sse_teardown_unsubscribes_from_the_live_network() -> None:
  """Closing SSE after reset unsubscribes the queue from the live network, not the old one."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    created = await client.post("/user/sessions", json={"user_id": 1})
    token = created.json()["token"]
    response = await events(session_request(app, token))
    agen = response.body_iterator
    try:
      assert len(app.state.network._subscribers) == 1
      await client.post("/admin/reset")
      assert len(app.state.network._subscribers) == 1
      chunk = await agen.__anext__()
      text = chunk if isinstance(chunk, str) else chunk.decode()
      assert '"type": "reset"' in text
    finally:
      await agen.aclose()
    assert app.state.network._subscribers == []
