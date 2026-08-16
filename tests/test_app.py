from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.errors import CANT_INITIATE, MESSAGE_TO_EDIT

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


@pytest.fixture
async def client():
  app = create_app()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://tg") as ac:
    ac.app = app
    yield ac


def _open_private(client, token: str = TOKEN, user_id: int = 42) -> None:
  net = client.app.state.network
  if user_id not in net.users:
    net.create_user(id=user_id, first_name="Test")
  if net.bot_by_token(token) is None:
    net.create_bot(token=token)
  net.ensure_private_chat(user_id, int(token.split(":")[0]))


async def test_get_me_returns_bot_user(client) -> None:
  response = await client.post(f"/bot{TOKEN}/getMe")
  payload = response.json()
  assert payload["ok"] is True
  assert payload["result"]["is_bot"] is True
  assert payload["result"]["id"] == 111111111


async def test_send_message_is_recorded_and_returns_message(client) -> None:
  net = client.app.state.network
  net.create_user(id=42, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(42, 111111111)
  response = await client.post(
    f"/bot{TOKEN}/sendMessage",
    data={"chat_id": "42", "text": "Hello"},
  )
  payload = response.json()
  assert payload["ok"] is True
  result = payload["result"]
  assert result["text"] == "Hello"
  assert result["from"]["is_bot"] is True
  assert result["from"]["id"] == 111111111
  assert set(result) == {"message_id", "date", "chat", "text", "from"}
  assert [m["text"] for m in net.bot_chats[(42, 111111111)]] == ["Hello"]


async def test_tokens_do_not_share_state(client) -> None:
  _open_private(client, TOKEN, 42)
  await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "to-bot"})
  net = client.app.state.network
  net.create_bot(token=ALERT)
  net.ensure_outbound_chat(-100123, 222222222)
  await client.post(f"/bot{ALERT}/sendMessage", data={"chat_id": "-100123", "text": "alert"})
  assert [m["text"] for m in net.bot_chats[(42, 111111111)]] == ["to-bot"]
  assert [m["text"] for m in net.bot_chats[(-100123, 222222222)]] == ["alert"]


async def test_inline_keyboard_is_parsed_into_buttons(client) -> None:
  _open_private(client)
  markup = {"inline_keyboard": [[{"text": "Order", "callback_data": "svc:7:order"}]]}
  await client.post(
    f"/bot{TOKEN}/sendMessage",
    data={"chat_id": "42", "text": "Card", "reply_markup": json.dumps(markup)},
  )
  msg = client.app.state.network.bot_chats[(42, 111111111)][-1]
  assert msg["reply_markup"]["inline_keyboard"][0][0]["text"] == "Order"
  assert msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "svc:7:order"


async def test_reply_keyboard_is_parsed(client) -> None:
  _open_private(client)
  markup = {"keyboard": [[{"text": "KM estimates"}]], "resize_keyboard": True}
  await client.post(
    f"/bot{TOKEN}/sendMessage",
    data={"chat_id": "42", "text": "Menu", "reply_markup": json.dumps(markup)},
  )
  net = client.app.state.network
  msg = net.bot_chats[(42, 111111111)][-1]
  assert msg["reply_markup"]["keyboard"][0][0]["text"] == "KM estimates"
  assert net.reply_keyboard(42, 111111111) == [["KM estimates"]]


async def test_send_message_result_omits_reply_keyboard(client) -> None:
  """Bot API Message.reply_markup is InlineKeyboardMarkup only."""
  _open_private(client)
  markup = {"keyboard": [[{"text": "KM estimates"}]], "resize_keyboard": True}
  response = await client.post(
    f"/bot{TOKEN}/sendMessage",
    data={"chat_id": "42", "text": "Menu", "reply_markup": json.dumps(markup)},
  )
  result = response.json()["result"]
  assert "reply_markup" not in result
  stored = client.app.state.network.bot_chats[(42, 111111111)][-1]
  assert stored["reply_markup"]["keyboard"][0][0]["text"] == "KM estimates"


async def test_send_message_result_omits_keyboard_remove(client) -> None:
  _open_private(client)
  response = await client.post(
    f"/bot{TOKEN}/sendMessage",
    data={
      "chat_id": "42",
      "text": "form",
      "reply_markup": json.dumps({"remove_keyboard": True}),
    },
  )
  assert "reply_markup" not in response.json()["result"]


async def test_get_updates_returns_pushed_update_and_advances_offset(client) -> None:
  net = client.app.state.network

  response = await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
  assert response.json()["result"] == []

  net.push_update(TOKEN, {"message": {"text": "/start"}})
  response = await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
  updates = response.json()["result"]
  assert len(updates) == 1
  assert updates[0]["message"]["text"] == "/start"

  offset = updates[0]["update_id"] + 1
  response = await client.post(
    f"/bot{TOKEN}/getUpdates", data={"timeout": "0", "offset": str(offset)}
  )
  assert response.json()["result"] == []
  assert client.app.state.network is net


async def test_unknown_method_fails_loudly(client) -> None:
  response = await client.post(f"/bot{TOKEN}/sendPoll", data={"chat_id": "42"})
  assert response.status_code == 404
  assert response.json()["ok"] is False
  assert response.json()["error_code"] == 404
  journal = client.app.state.network.journal.unimplemented()
  assert any(r.method == "sendPoll" and r.kind == "unimplemented" for r in journal)


async def test_bad_token_is_unauthorized(client) -> None:
  response = await client.post("/botnot-a-token/getMe")
  assert response.status_code == 401


async def test_bot_cannot_initiate_private_without_dialog(client) -> None:
  client.app.state.network.create_user(id=42, first_name="Test")
  response = await client.post(
    f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "Hello"}
  )
  assert response.status_code == 403
  assert response.json()["description"] == CANT_INITIATE


async def test_inbound_private_update_opens_dialog_for_reply(client) -> None:
  net = client.app.state.network
  net.create_bot(token=TOKEN)
  net.push_update(
    TOKEN,
    {
      "message": {
        "text": "/start",
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Vasily", "username": "bridge_user"},
      }
    },
  )
  response = await client.post(
    f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "Hello"}
  )
  assert response.status_code == 200
  assert response.json()["result"]["text"] == "Hello"
  assert net.users[42]["username"] == "bridge_user"


async def test_edit_missing_message_is_canonical(client) -> None:
  _open_private(client)
  response = await client.post(
    f"/bot{TOKEN}/editMessageText",
    data={"chat_id": "42", "message_id": "1", "text": "no"},
  )
  assert response.status_code == 400
  assert response.json()["description"] == MESSAGE_TO_EDIT


async def test_get_file_returns_downloadable_path(client) -> None:
  response = await client.post(f"/bot{TOKEN}/getFile", data={"file_id": "photo-1"})
  file_path = response.json()["result"]["file_path"]

  download = await client.get(f"/file/bot{TOKEN}/{file_path}")
  assert download.status_code == 200
  assert download.content == b"e2e-file-content"


async def test_get_user_profile_photos_returns_downloadable_avatar(client) -> None:
  response = await client.post(
    f"/bot{TOKEN}/getUserProfilePhotos",
    data={"user_id": "42", "limit": "1"},
  )
  payload = response.json()
  assert payload["ok"] is True
  assert payload["result"]["total_count"] == 1
  file_id = payload["result"]["photos"][0][0]["file_id"]

  file_response = await client.post(f"/bot{TOKEN}/getFile", data={"file_id": file_id})
  file_path = file_response.json()["result"]["file_path"]
  download = await client.get(f"/file/bot{TOKEN}/{file_path}")
  assert download.status_code == 200
  assert download.content == b"e2e-avatar-content"


async def test_queued_error_makes_next_send_fail(client) -> None:
  _open_private(client)
  client.app.state.network.inject_error(
    TOKEN, 42, 403, {"ok": False, "error_code": 403, "description": "bot was blocked by the user"}
  )

  response = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "x"})
  assert response.status_code == 403


async def test_health_endpoint(client) -> None:
  response = await client.get("/health")
  assert response.status_code == 200
  assert response.json() == {"status": "ok"}


async def test_admin_routes_drive_compose_smoke(client) -> None:
  chat_id = 900900
  await client.post("/admin/reset")
  pushed = await client.post(f"/admin/{TOKEN}/updates", json={"chat_id": chat_id, "text": "/start"})
  assert pushed.status_code == 200

  updates = (await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})).json()
  assert len(updates["result"]) == 1
  assert updates["result"][0]["message"]["text"] == "/start"


async def test_root_serves_web_client(client) -> None:
  response = await client.get("/")
  assert response.status_code == 200
  assert "telemulator" in response.text
  assert "Telegram" not in response.text
  health = await client.get("/health")
  assert health.json() == {"status": "ok"}


async def test_web_client_mentions_debug_console(client) -> None:
  html = (await client.get("/")).text
  js = (await client.get("/app.js")).text
  assert "debug" in html
  assert "/admin/journal" in js or "journal" in js


async def test_web_client_restores_the_session_on_reload(client) -> None:
  js = (await client.get("/app.js")).text
  assert "/user/me" in js
  assert "localStorage" in js
  # Storage is parsed via savedAccounts: a corrupt value must not crash
  # restore() before the first paint and leave a blank tab.
  assert "accounts.push(...savedAccounts())" in js


async def test_web_client_tracks_spinners_per_query(client) -> None:
  js = (await client.get("/app.js")).text
  assert "new Map()" in js
  assert "pending.set(queryId, btn)" in js
  assert "new Option(" in js


async def test_journal_hole_is_listed_in_both_admin_lists(client) -> None:
  """The console must subtract the intersection: otherwise one hole is two rows."""
  await client.post(f"/bot{TOKEN}/sendPoll", data={"chat_id": "1"})
  journal = (await client.get("/admin/journal")).json()
  assert [r["method"] for r in journal["calls"]] == ["sendPoll"]
  assert [r["method"] for r in journal["unimplemented"]] == ["sendPoll"]
  js = (await client.get("/app.js")).text
  assert 'rec.kind !== "unimplemented"' in js
