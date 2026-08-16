from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient
import pytest

from asgi import session_request
from telemulator import create_app
from telemulator.network import Network
from telemulator.user_api import send_text
from telemulator.user_http import events

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def test_send_text_to_unknown_negative_does_not_create_user() -> None:
  net = Network()
  net.create_user(id=1, first_name="А")
  with pytest.raises(KeyError):
    send_text(net, 1, -99, "привет")
  assert -99 not in net.users
  assert -99 not in net.chats


def test_send_text_writes_group_thread_and_emits_card() -> None:
  net = Network()
  net.create_user(id=1, first_name="А")
  net.create_user(id=2, first_name="Б")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1, member_ids=[2])
  q = net.subscribe()
  send_text(net, 1, chat.id, "привет")
  assert chat.messages[-1]["from"]["id"] == 1
  assert chat.messages[-1]["chat"] == {"id": chat.id, "type": "supergroup", "title": "S"}
  first = q.get_nowait()
  second = q.get_nowait()
  assert {first["viewer_id"], second["viewer_id"]} == {1, 2}
  assert first["peer_id"] == chat.id
  assert first["message"]["chat"]["title"] == "S"
  assert "first_name" not in first["message"]["chat"]
  net.unsubscribe(q)


def test_non_member_send_raises_permission() -> None:
  net = Network()
  net.create_user(id=1, first_name="А")
  net.create_user(id=2, first_name="Б")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  with pytest.raises(PermissionError):
    send_text(net, 2, chat.id, "нет")


async def test_http_creates_supergroup_and_lists_title() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/user/sessions", json={"user_id": 1})
    bad = await client.post("/user/chats", json={"type": "private", "title": "x"})
    assert bad.status_code == 400
    created = await client.post(
      "/user/chats", json={"type": "supergroup", "title": "Команда", "member_ids": [2]}
    )
    assert created.status_code == 200
    chat = created.json()["chat"]
    assert chat["type"] == "supergroup"
    assert chat["title"] == "Команда"
    assert chat["id"] < 0
    assert "first_name" not in chat
    listed = {c["id"]: c for c in (await client.get("/user/chats")).json()["chats"]}
    assert listed[chat["id"]]["title"] == "Команда"
    sent = await client.post(
      f"/user/chats/{chat['id']}/messages",
      json={"text": "привет"},
    )
    assert sent.json()["message"]["chat"]["title"] == "Команда"
    await client.post("/user/sessions", json={"user_id": 2})
    feed = (await client.get(f"/user/chats/{chat['id']}/messages")).json()
    assert feed["messages"][-1]["text"] == "привет"
    outsider = await client.post("/admin/users", json={"id": 3, "first_name": "В"})
    await client.post("/user/sessions", json={"user_id": 3})
    denied = await client.post(
      f"/user/chats/{chat['id']}/messages", json={"text": "нет"}
    )
    assert denied.status_code == 403
    missing = await client.post("/user/chats/-50/messages", json={"text": "нет"})
    assert missing.status_code == 400


async def test_sse_group_event_uses_chat_card() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    token = (await client.post("/user/sessions", json={"user_id": 1})).json()["token"]
    queue = app.state.network.subscribe()
    await client.post(f"/user/chats/{chat['id']}/messages", json={"text": "ping"})
    event = queue.get_nowait()
    app.state.network.unsubscribe(queue)
    assert event["peer_id"] == chat["id"]
    assert event["message"]["chat"]["type"] == "supergroup"
    response = await events(session_request(app, token))
    assert response.headers["content-type"].startswith("text/event-stream")
    await response.body_iterator.aclose()


async def test_members_crud_and_admin_mirror() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Club"})
    created = await client.post(
      "/admin/chats",
      json={"type": "supergroup", "title": "S", "creator_id": 1},
    )
    assert created.status_code == 200
    chat_id = created.json()["chat"]["id"]
    await client.post("/user/sessions", json={"user_id": 1})
    added = await client.post(
      f"/user/chats/{chat_id}/members",
      json={"user_id": 111111111, "status": "administrator"},
    )
    assert added.status_code == 200
    assert added.json()["member"]["status"] == "administrator"
    assert "can_be_edited" not in added.json()["member"]
    listing = (await client.get(f"/user/chats/{chat_id}/members")).json()["members"]
    assert {m["user"]["id"] for m in listing} == {1, 111111111}
    patched = await client.patch(
      f"/user/chats/{chat_id}/members/111111111",
      json={"status": "member"},
    )
    assert patched.json()["member"]["status"] == "member"
    await client.post(
      f"/user/chats/{chat_id}/members", json={"user_id": 2}
    )
    await client.post("/admin/bots", json={"token": "222222222:tok", "first_name": "Alert"})
    await client.post("/user/sessions", json={"user_id": 2})
    bot_by_member = await client.post(
      f"/user/chats/{chat_id}/members", json={"user_id": 222222222}
    )
    assert bot_by_member.status_code == 403
    await client.post("/user/sessions", json={"user_id": 1})
    await client.delete(f"/user/chats/{chat_id}/members/2")
    ids = {
      m["user"]["id"]
      for m in (await client.get(f"/user/chats/{chat_id}/members")).json()["members"]
    }
    assert 2 not in ids
    await client.post("/user/sessions", json={"user_id": 2})
    denied = await client.post(
      f"/user/chats/{chat_id}/members", json={"user_id": 2}
    )
    assert denied.status_code == 403
    await client.post("/user/sessions", json={"user_id": 1})
    owner_patch = await client.patch(
      f"/user/chats/{chat_id}/members/1", json={"status": "member"}
    )
    assert owner_patch.status_code == 400


async def test_channel_kicked_cannot_readd_without_unban() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    chat = (
      await client.post(
        "/admin/chats",
        json={"type": "channel", "title": "C", "creator_id": 1, "member_ids": [2]},
      )
    ).json()["chat"]
    await client.delete(f"/admin/chats/{chat['id']}/members/2")
    assert app.state.network.chats[chat["id"]].members[2].status == "kicked"
    again = await client.post(
      f"/admin/chats/{chat['id']}/members", json={"user_id": 2}
    )
    assert again.status_code == 400


async def test_creator_in_member_ids_stays_creator() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats",
        json={"type": "supergroup", "title": "S", "member_ids": [1, 2, 2]},
      )
    ).json()["chat"]
    members = app.state.network.chats[chat["id"]].members
    assert members[1].status == "creator"
    assert members[2].status == "member"
    assert len(members) == 2
    gone = await client.request("DELETE", f"/user/chats/{chat['id']}/members/2")
    assert gone.status_code == 200


async def test_adding_active_member_again_is_400_and_keeps_rights() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats",
        json={
          "type": "supergroup",
          "title": "S",
          "members": [{"user_id": 2, "status": "administrator", "can_change_info": True}],
        },
      )
    ).json()["chat"]
    again = await client.post(
      f"/user/chats/{chat['id']}/members", json={"user_id": 2}
    )
    assert again.status_code == 400
    member = app.state.network.chats[chat["id"]].members[2]
    assert member.status == "administrator"
    assert member.can_change_info is True


async def test_delete_member_without_record_is_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    missing = await client.request("DELETE", f"/user/chats/{chat['id']}/members/2")
    assert missing.status_code == 400
    admin = await client.request("DELETE", f"/admin/chats/{chat['id']}/members/2")
    assert admin.status_code == 400


async def test_left_member_can_be_added_again() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "group", "title": "G", "member_ids": [2]}
      )
    ).json()["chat"]
    await client.request("DELETE", f"/user/chats/{chat['id']}/members/2")
    assert app.state.network.chats[chat["id"]].members[2].status == "left"
    back = await client.post(f"/user/chats/{chat['id']}/members", json={"user_id": 2})
    assert back.status_code == 200
    assert app.state.network.chats[chat["id"]].members[2].status == "member"
