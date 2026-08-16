from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.errors import (
  CANT_PROMOTE_OWNER,
  CANT_REMOVE_OWNER,
  METHOD_SUPER_CHANNEL,
  NOT_MEMBER_CHANNEL,
  USER_NOT_FOUND,
)

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


async def test_get_chat_private_and_group_shapes() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 42, "first_name": "Анна", "username": "anna"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 42, "bot_token": TOKEN})
    priv = (await client.post(f"/bot{TOKEN}/getChat", data={"chat_id": "42"})).json()["result"]
    assert priv["type"] == "private"
    assert priv["first_name"] == "Анна"
    assert "title" not in priv
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    g = (await client.post(f"/bot{TOKEN}/getChat", data={"chat_id": str(chat["id"])})).json()["result"]
    assert g == {"id": chat["id"], "type": "supergroup", "title": "S"}


async def test_get_chat_member_left_self_200_never_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(chat["id"], 111111111, actor_id=1)
    net.remove_member(chat["id"], 111111111, actor_id=111111111)
    left = await client.post(
      f"/bot{TOKEN}/getChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "111111111"},
    )
    assert left.status_code == 200
    assert left.json()["result"]["status"] == "left"
    missing = await client.post(
      f"/bot{TOKEN}/getChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "2"},
    )
    assert missing.status_code == 400
    assert missing.json()["description"] == USER_NOT_FOUND


async def test_get_chat_administrators_hides_other_bots_by_default() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/bots", json={"token": ALERT})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(chat["id"], 111111111, actor_id=1, status="administrator")
    net.add_member(chat["id"], 222222222, actor_id=1, status="administrator")
    ids = [
      m["user"]["id"]
      for m in (
        await client.post(
          f"/bot{TOKEN}/getChatAdministrators", data={"chat_id": str(chat["id"])}
        )
      ).json()["result"]
    ]
    assert 1 in ids
    assert 111111111 in ids
    assert 222222222 not in ids
    with_bots = (
      await client.post(
        f"/bot{TOKEN}/getChatAdministrators",
        data={"chat_id": str(chat["id"]), "return_bots": "true"},
      )
    ).json()["result"]
    assert {m["user"]["id"] for m in with_bots} >= {1, 111111111, 222222222}


async def test_get_chat_member_count_skips_left() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "supergroup", "title": "S", "member_ids": [2]}
      )
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    app.state.network.remove_member(chat["id"], 2, actor_id=1)
    count = (
      await client.post(
        f"/bot{TOKEN}/getChatMemberCount", data={"chat_id": str(chat["id"])}
      )
    ).json()["result"]
    assert count == 2


async def test_promote_on_group_is_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "group", "title": "G", "member_ids": [2]}
      )
    ).json()["chat"]
    app.state.network.add_member(
      chat["id"], 111111111, actor_id=1, status="administrator",
      flags={"can_promote_members": True},
    )
    r = await client.post(
      f"/bot{TOKEN}/promoteChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "2", "can_delete_messages": "true"},
    )
    assert r.status_code == 400
    assert r.json()["description"] == METHOD_SUPER_CHANNEL


async def test_ban_and_promote_owner_are_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(
      chat["id"], 111111111, actor_id=1, status="administrator",
      flags={"can_restrict_members": True, "can_promote_members": True},
    )
    ban = await client.post(
      f"/bot{TOKEN}/banChatMember", data={"chat_id": str(chat["id"]), "user_id": "1"}
    )
    assert ban.status_code == 400
    assert ban.json()["description"] == CANT_REMOVE_OWNER
    promo = await client.post(
      f"/bot{TOKEN}/promoteChatMember", data={"chat_id": str(chat["id"]), "user_id": "1"}
    )
    assert promo.status_code == 400
    assert promo.json()["description"] == CANT_PROMOTE_OWNER


async def test_ban_in_basic_group_is_left_readd_without_unban() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "group", "title": "G", "member_ids": [2]}
      )
    ).json()["chat"]
    app.state.network.add_member(
      chat["id"], 111111111, actor_id=1, status="administrator",
      flags={"can_restrict_members": True},
    )
    await client.post(
      f"/bot{TOKEN}/banChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
    )
    assert app.state.network.chats[chat["id"]].members[2].status == "left"
    unban = await client.post(
      f"/bot{TOKEN}/unbanChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
    )
    assert unban.status_code == 400
    app.state.network.add_member(chat["id"], 2, actor_id=1)
    assert app.state.network.chats[chat["id"]].members[2].status == "member"


async def test_unban_kicked_keeps_left_record_and_kicks_living() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/users", json={"id": 3, "first_name": "В"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats", json={"type": "supergroup", "title": "S", "member_ids": [2, 3]}
      )
    ).json()["chat"]
    app.state.network.add_member(
      chat["id"], 111111111, actor_id=1, status="administrator",
      flags={"can_restrict_members": True},
    )
    await client.post(
      f"/bot{TOKEN}/banChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
    )
    assert app.state.network.chats[chat["id"]].members[2].status == "kicked"
    with pytest.raises(PermissionError):
      app.state.network.add_member(chat["id"], 2, actor_id=1)
    unban = await client.post(
      f"/bot{TOKEN}/unbanChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
    )
    assert unban.status_code == 200
    rec = app.state.network.chats[chat["id"]].members[2]
    assert rec.status == "left"
    assert 2 in app.state.network.chats[chat["id"]].members
    living = await client.post(
      f"/bot{TOKEN}/unbanChatMember", data={"chat_id": str(chat["id"]), "user_id": "3"}
    )
    assert living.status_code == 200
    assert app.state.network.chats[chat["id"]].members[3].status == "left"
    keep = await client.post(
      f"/bot{TOKEN}/unbanChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "1", "only_if_banned": "true"},
    )
    assert keep.status_code == 200
    assert app.state.network.chats[chat["id"]].members[1].status == "creator"


async def test_channel_restrict_default_true_and_demote_bot_leaves() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "channel", "title": "C"})
    ).json()["chat"]
    net = app.state.network
    net.add_member(
      chat["id"], 111111111, actor_id=1, flags={"can_promote_members": True, "can_post_messages": True}
    )
    net.add_member(chat["id"], 2, actor_id=1)
    await client.post(
      f"/bot{TOKEN}/promoteChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "2"},
    )
    human = net.chats[chat["id"]].members[2]
    assert human.status == "administrator"
    assert human.can_restrict_members is True
    assert human.promoted_by_bot_id == 111111111
    gm = (
      await client.post(
        f"/bot{TOKEN}/getChatMember",
        data={"chat_id": str(chat["id"]), "user_id": "2"},
      )
    ).json()["result"]
    assert gm["can_be_edited"] is True
    net.bots[TOKEN].updates.clear()
    await client.post(
      f"/bot{TOKEN}/promoteChatMember",
      data={
        "chat_id": str(chat["id"]),
        "user_id": "111111111",
        "can_post_messages": "false",
        "can_restrict_members": "false",
        "can_promote_members": "false",
      },
    )
    self_m = net.chats[chat["id"]].members[111111111]
    assert self_m.status == "left"
    assert any("my_chat_member" in u for u in net.bots[TOKEN].updates)
    sent = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "x"}
    )
    assert sent.status_code == 403
    assert sent.json()["description"] == NOT_MEMBER_CHANNEL


async def test_my_chat_member_on_add_and_kicked_gets_no_leave_message() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
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
    net.bots[TOKEN].updates.clear()
    net.bots[ALERT].updates.clear()
    net.add_member(chat["id"], 111111111, actor_id=1)
    assert any("my_chat_member" in u for u in net.bots[TOKEN].updates)
    assert not any("chat_member" in u for u in net.bots[TOKEN].updates)
    net.remove_member(chat["id"], 111111111, actor_id=1)
    leave_msgs = [
      u for u in net.bots[TOKEN].updates
      if u.get("message", {}).get("left_chat_member")
    ]
    assert leave_msgs == []
    assert any(
      u.get("my_chat_member", {}).get("new_chat_member", {}).get("status") in {"left", "kicked"}
      for u in net.bots[TOKEN].updates
    )


async def test_user_patch_drops_can_be_edited_from_earlier_bot_promote() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post(
        "/user/chats",
        json={
          "type": "supergroup",
          "title": "S",
          "members": [
            {"user_id": 2},
            {
              "user_id": 111111111,
              "status": "administrator",
              "can_promote_members": True,
            },
          ],
        },
      )
    ).json()["chat"]
    await client.post(
      f"/bot{TOKEN}/promoteChatMember",
      data={"chat_id": str(chat["id"]), "user_id": "2", "can_change_info": "true"},
    )
    mine = (
      await client.post(
        f"/bot{TOKEN}/getChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
      )
    ).json()["result"]
    assert mine["can_be_edited"] is True
    patched = await client.patch(
      f"/user/chats/{chat['id']}/members/2",
      json={"status": "administrator", "can_delete_messages": True},
    )
    assert patched.status_code == 200
    after = (
      await client.post(
        f"/bot{TOKEN}/getChatMember", data={"chat_id": str(chat["id"]), "user_id": "2"}
      )
    ).json()["result"]
    assert after["can_be_edited"] is False
    assert after["can_delete_messages"] is True
    assert after["can_change_info"] is False
    assert app.state.network.chats[chat["id"]].members[2].promoted_by_bot_id is None
