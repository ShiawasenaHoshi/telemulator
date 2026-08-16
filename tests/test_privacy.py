from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.network import Network
from telemulator.user_api import send_text

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


def _group_with_two_privacy_bots() -> tuple[Network, int]:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_bot(token=TOKEN, first_name="Club", username="clubbot")
  net.create_bot(token=ALERT, first_name="Alert", username="alertbot")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  net.add_member(chat.id, 111111111, actor_id=1)
  net.add_member(chat.id, 222222222, actor_id=1)
  net.bots[TOKEN].updates.clear()
  net.bots[ALERT].updates.clear()
  return net, chat.id


def _texts(net: Network, token: str) -> list[str]:
  return [
    u["message"]["text"]
    for u in net.bots[token].updates
    if "message" in u and "text" in u["message"]
  ]


def test_cmd_at_mention_reaches_that_bot_only_among_privacy_bots() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  send_text(net, 1, chat_id, "/start@clubbot")
  assert "/start@clubbot" in _texts(net, TOKEN)
  assert _texts(net, ALERT) == []


def test_bare_cmd_goes_to_last_bot_id_only() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  net.chats[chat_id].last_bot_id = 222222222
  send_text(net, 1, chat_id, "/start")
  assert _texts(net, TOKEN) == []
  assert "/start" in _texts(net, ALERT)


def test_cmd_in_the_middle_and_plain_text_and_mention_are_silent() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  send_text(net, 1, chat_id, "see /start@clubbot")
  send_text(net, 1, chat_id, "hi")
  send_text(net, 1, chat_id, "@clubbot hi")
  assert _texts(net, TOKEN) == []
  assert _texts(net, ALERT) == []


def test_reply_to_bot_beats_cmd_at_other_among_step3() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  chat = net.chats[chat_id]
  chat.messages.append(
    {
      "message_id": 50,
      "from": dict(net.users[111111111]),
      "chat": {"id": chat.id, "type": "supergroup", "title": "S"},
      "text": "from club",
    }
  )
  send_text(net, 1, chat_id, "/start@alertbot", reply_to_message_id=50)
  assert any("/start@alertbot" in t for t in _texts(net, TOKEN))
  assert _texts(net, ALERT) == []


def test_admin_bot_and_privacy_off_still_see_step3_message() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  net.create_bot(token="333333333:tok", first_name="Admin", username="adminbot")
  net.add_member(
    chat_id, 333333333, actor_id=1, status="administrator", flags={"can_delete_messages": True}
  )
  net.bots["333333333:tok"].updates.clear()
  net.chats[chat_id].privacy_at_join[222222222] = False
  send_text(net, 1, chat_id, "chatter")
  assert "chatter" in _texts(net, "333333333:tok")
  assert "chatter" in _texts(net, ALERT)
  assert _texts(net, TOKEN) == []


def test_other_bot_messages_are_not_delivered() -> None:
  net, chat_id = _group_with_two_privacy_bots()
  chat = net.chats[chat_id]
  from telemulator.privacy import deliver_group_message_to_bots
  deliver_group_message_to_bots(
    net,
    chat,
    {
      "message_id": 9,
      "from": dict(net.users[111111111]),
      "chat": {"id": chat.id, "type": chat.type, "title": chat.title},
      "text": "bot secret",
    },
  )
  assert _texts(net, ALERT) == []
  assert _texts(net, TOKEN) == []


def test_service_reaches_privacy_bot_while_in_chat() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_bot(token=TOKEN, first_name="Club", username="clubbot")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  net.add_member(chat.id, 111111111, actor_id=1)
  net.bots[TOKEN].updates.clear()
  net.add_member(chat.id, 2, actor_id=1)
  assert any("new_chat_members" in u.get("message", {}) for u in net.bots[TOKEN].updates)


def test_privacy_post_does_not_rewrite_existing_joins() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_bot(token=TOKEN, first_name="Club", username="clubbot")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  net.add_member(chat.id, 111111111, actor_id=1)
  net.bots[TOKEN].privacy_mode = False
  send_text(net, 1, chat.id, "after change without rejoin")
  assert [u for u in net.bots[TOKEN].updates if u.get("message", {}).get("text") == "after change without rejoin"] == []


async def test_admin_privacy_and_get_me_flags() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN, "privacy": True})
    me = (await client.post(f"/bot{TOKEN}/getMe")).json()["result"]
    assert me["can_join_groups"] is True
    assert me["can_read_all_group_messages"] is False
    await client.post("/admin/bots/privacy", json={"token": TOKEN, "privacy": False})
    me2 = (await client.post(f"/bot{TOKEN}/getMe")).json()["result"]
    assert me2["can_read_all_group_messages"] is True
    snap = (await client.post("/admin/snapshot")).json()
    bot = next(b for b in snap["bots"] if b["token"] == TOKEN)
    assert bot["privacy_mode"] is False


def test_readd_picks_up_new_privacy_mode() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_bot(token=TOKEN, first_name="Club", username="clubbot")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  net.add_member(chat.id, 111111111, actor_id=1)
  # A third-party remove in a supergroup yields kicked — without unban, add_member fails (Task 2).
  net.remove_member(chat.id, 111111111, actor_id=111111111)
  net.bots[TOKEN].privacy_mode = False
  net.add_member(chat.id, 111111111, actor_id=1)
  assert chat.privacy_at_join[111111111] is False
  net.bots[TOKEN].updates.clear()
  send_text(net, 1, chat.id, "chatter")
  assert any(u.get("message", {}).get("text") == "chatter" for u in net.bots[TOKEN].updates)


def test_dump_load_reset_preserve_privacy_mode() -> None:
  net = Network()
  net.create_bot(token=TOKEN, first_name="Club")
  assert net.bots[TOKEN].user["username"] == "telemulator111111111"
  net.bots[TOKEN].privacy_mode = False
  restored = Network()
  restored.load(net.dump())
  assert restored.bots[TOKEN].privacy_mode is False
  fresh = restored.reset()
  assert fresh.bots[TOKEN].privacy_mode is False
  assert fresh.bots[TOKEN].user["username"] == "telemulator111111111"


def test_old_snapshot_privacy_mode_defaults_true() -> None:
  net = Network()
  net.load(
    {
      "users": [{"id": 111111111, "is_bot": True, "first_name": "Club"}],
      "bots": [{"token": TOKEN, "user": {"id": 111111111, "is_bot": True, "first_name": "Club"}}],
    }
  )
  assert net.bots[TOKEN].privacy_mode is True
