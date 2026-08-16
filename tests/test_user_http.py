from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from telemulator import create_app

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_session_lists_chats_and_sends_p2p_visible_to_both() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Club"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})

    unauth = await client.get("/user/me")
    assert unauth.status_code == 401

    created = await client.post("/user/sessions", json={"user_id": 1})
    assert created.status_code == 200
    assert created.json()["user"]["first_name"] == "А"
    me = await client.get("/user/me")
    assert me.json()["id"] == 1

    chats = {c["id"]: c for c in (await client.get("/user/chats")).json()["chats"]}
    assert chats[111111111]["first_name"] == "Club"

    await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "меню"})
    feed = (
      await client.get("/user/chats/111111111/messages")
    ).json()
    assert feed["messages"][0]["text"] == "меню"
    assert feed["messages"][0]["chat"]["id"] == 111111111

    await client.post("/user/sessions", json={"user_id": 1})
    sent = await client.post("/user/chats/2/messages", json={"text": "привет"})
    assert sent.json()["message"]["chat"]["id"] == 2
    assert sent.json()["message"]["from"]["id"] == 1

    await client.post("/user/sessions", json={"user_id": 2})
    other = (await client.get("/user/chats/1/messages")).json()["messages"]
    assert other[0]["text"] == "привет"
    assert other[0]["chat"]["id"] == 1


async def test_press_enqueues_callback_and_answer_clears_it() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    markup = {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]}
    await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": "1", "text": "?", "reply_markup": json.dumps(markup)},
    )
    await client.post("/user/sessions", json={"user_id": 1})
    pressed = await client.post(
      "/user/chats/111111111/messages/1/press", json={"data": "yes"}
    )
    assert pressed.status_code == 200
    query_id = pressed.json()["query_id"]
    updates = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    assert updates[-1]["callback_query"]["id"] == query_id
    assert updates[-1]["callback_query"]["data"] == "yes"
    answered = await client.post(
      f"/bot{TOKEN}/answerCallbackQuery", data={"callback_query_id": query_id}
    )
    assert answered.json() == {"ok": True, "result": True}
    late = await client.post(
      f"/bot{TOKEN}/answerCallbackQuery", data={"callback_query_id": query_id}
    )
    assert late.status_code == 400
    assert "too old" in late.json()["description"]


async def test_press_unknown_button_is_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "нет кнопок"})
    await client.post("/user/sessions", json={"user_id": 1})
    res = await client.post(
      "/user/chats/111111111/messages/1/press", json={"data": "nope"}
    )
    assert res.status_code == 400


async def test_reply_keyboard_is_null_in_p2p_even_if_bot_dialog_has_one() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Club"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    markup = {"keyboard": [[{"text": "Расчёты КМ"}]], "resize_keyboard": True}
    await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": "1", "text": "меню", "reply_markup": json.dumps(markup)},
    )
    await client.post("/user/sessions", json={"user_id": 1})
    await client.post("/user/chats/2/messages", json={"text": "привет"})

    bot_feed = (await client.get("/user/chats/111111111/messages")).json()
    assert bot_feed["reply_keyboard"] == [["Расчёты КМ"]]
    p2p_feed = (await client.get("/user/chats/2/messages")).json()
    assert p2p_feed["reply_keyboard"] is None


async def test_send_photo_bytes_are_downloadable_via_user_files() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    sent = await client.post(
      f"/bot{TOKEN}/sendPhoto", data={"chat_id": "1", "photo": "x"}
    )
    assert sent.status_code == 200
    file_id = sent.json()["result"]["photo"][0]["file_id"]
    await client.post("/user/sessions", json={"user_id": 1})
    download = await client.get(f"/user/files/{file_id}.bin")
    assert download.status_code == 200
    assert download.content


async def test_rejected_send_photo_leaves_no_downloadable_file() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    await client.post(
      "/admin/errors",
      json={
        "token": TOKEN,
        "chat_id": 1,
        "status": 403,
        "body": {"ok": False, "error_code": 403, "description": "Forbidden"},
      },
    )
    sent = await client.post(f"/bot{TOKEN}/sendPhoto", data={"chat_id": "1", "photo": "x"})
    assert sent.status_code == 403

    await client.post("/user/sessions", json={"user_id": 1})
    assert (await client.get("/user/files/photo-1.bin")).status_code == 404
