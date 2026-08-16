from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
import pytest

from telemulator import create_app
from telemulator.errors import (
  KICKED_CHANNEL,
  NOT_ENOUGH_RIGHTS_SEND,
  NOT_MEMBER_CHANNEL,
  NOT_MEMBER_GROUP,
  NOT_MEMBER_SUPER,
)

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


@pytest.mark.parametrize(
  "chat_type,status,expected",
  [
    ("group", "left", NOT_MEMBER_GROUP),
    ("supergroup", "left", NOT_MEMBER_SUPER),
    ("channel", "left", NOT_MEMBER_CHANNEL),
    ("channel", "kicked", KICKED_CHANNEL),
  ],
)
async def test_send_membership_errors(chat_type, status, expected) -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": chat_type, "title": "T"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(chat["id"], 111111111, actor_id=1)
    member = net.chats[chat["id"]].members[111111111]
    member.status = status
    if status == "kicked":
      member.until_date = 0
    r = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "x"}
    )
    assert r.status_code == 403
    assert r.json()["description"] == expected


async def test_supergroup_send_sets_last_bot_id_and_delivers() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN, "username": "clubbot"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    sent = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "hi"}
    )
    assert sent.status_code == 200
    body = sent.json()["result"]
    assert body["chat"]["type"] == "supergroup"
    assert body["from"]["id"] == 111111111
    assert app.state.network.chats[chat["id"]].last_bot_id == 111111111
    assert (chat["id"], 111111111) not in app.state.network.bot_chats


async def test_channel_send_is_sender_chat_and_channel_post_to_others() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/bots", json={"token": ALERT})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "channel", "title": "C"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(chat["id"], 111111111, actor_id=1)
    net.add_member(chat["id"], 222222222, actor_id=1)
    sent = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "post"}
    )
    result = sent.json()["result"]
    assert "from" not in result
    assert result["sender_chat"]["id"] == chat["id"]
    assert result["sender_chat"]["title"] == "C"
    assert not any("channel_post" in u or "message" in u for u in net.bots[TOKEN].updates)
    posts = [u for u in net.bots[ALERT].updates if "channel_post" in u]
    assert posts[-1]["channel_post"]["text"] == "post"
    assert "from" not in posts[-1]["channel_post"]
    edited = await client.post(
      f"/bot{TOKEN}/editMessageText",
      data={"chat_id": str(chat["id"]), "message_id": str(result["message_id"]), "text": "edit"},
    )
    assert edited.status_code == 200
    assert any("edited_channel_post" in u for u in net.bots[ALERT].updates)
    assert not any("edited_channel_post" in u for u in net.bots[TOKEN].updates)


async def test_human_channel_post_is_channel_post_with_sender_chat() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats",
        json={"type": "channel", "title": "K", "members": [{"user_id": 111111111}]},
      )
    ).json()["chat"]
    posted = await client.post(
      f"/user/chats/{chat['id']}/messages", json={"text": "human post"}
    )
    assert posted.status_code == 200
    body = posted.json()["message"]
    assert "from" not in body
    assert body["sender_chat"] == {"id": chat["id"], "type": "channel", "title": "K"}
    updates = app.state.network.bots[TOKEN].updates
    assert not any("message" in u for u in updates)
    post = [u for u in updates if "channel_post" in u][-1]["channel_post"]
    assert post["text"] == "human post"
    assert "from" not in post
    assert post["sender_chat"]["id"] == chat["id"]


async def test_human_group_message_stays_message_with_from() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(
      chat["id"], 111111111, actor_id=1, status="administrator"
    )
    await client.post(f"/user/chats/{chat['id']}/messages", json={"text": "chatter"})
    updates = app.state.network.bots[TOKEN].updates
    said = [u for u in updates if u.get("message", {}).get("text") == "chatter"]
    assert said
    assert said[-1]["message"]["from"]["id"] == 1
    assert not any("channel_post" in u for u in updates)


async def test_channel_admin_without_post_is_403() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "channel", "title": "C"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(chat["id"], 111111111, actor_id=1)
    net.chats[chat["id"]].members[111111111].can_post_messages = False
    r = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "x"}
    )
    assert r.status_code == 403
    assert r.json()["description"] == NOT_ENOUGH_RIGHTS_SEND


async def test_group_reply_keyboard_written_for_each_member() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/users", json={"id": 2, "first_name": "B"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "supergroup", "title": "S", "member_ids": [2]}
      )
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    markup = {"keyboard": [[{"text": "Button"}]], "resize_keyboard": True}
    await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": str(chat["id"]), "text": "kb", "reply_markup": json.dumps(markup)},
    )
    net = app.state.network
    assert net.reply_keyboard(1, chat["id"]) == [["Button"]]
    assert net.reply_keyboard(2, chat["id"]) == [["Button"]]
    await client.post("/user/sessions", json={"user_id": 1})
    feed = (await client.get(f"/user/chats/{chat['id']}/messages")).json()
    assert feed["reply_keyboard"] == [["Button"]]


async def test_callback_in_group_has_non_private_chat() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    markup = {"inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]]}
    sent = await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": str(chat["id"]), "text": "?", "reply_markup": json.dumps(markup)},
    )
    mid = sent.json()["result"]["message_id"]
    await client.post(
      f"/user/chats/{chat['id']}/messages/{mid}/press", json={"data": "yes"}
    )
    updates = (
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    ).json()["result"]
    cb = [u for u in updates if "callback_query" in u][-1]
    assert cb["callback_query"]["message"]["chat"]["type"] == "supergroup"
    assert cb["callback_query"]["message"]["chat"]["id"] == chat["id"]
