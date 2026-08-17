from __future__ import annotations

import pytest

from telemulator.network import CALLBACK_TTL, SSE_QUEUE_LIMIT, Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_subscribers_get_emitted_events() -> None:
  net = Network()
  queue = net.subscribe()
  net.emit({"type": "journal", "record": {"method": "getMe"}})
  event = queue.get_nowait()
  assert event["type"] == "journal"
  net.unsubscribe(queue)


def test_journal_status_and_response_survive_snapshot_roundtrip() -> None:
  net = Network()
  net.journal.record(
    "sendMessage",
    TOKEN,
    "ok",
    {"text": "hi"},
    status=200,
    response={"ok": True, "result": {"message_id": 1}},
  )
  restored = Network()
  restored.load(net.dump())
  rec = restored.journal.calls()[-1]
  assert rec.status == 200
  assert rec.response == {"ok": True, "result": {"message_id": 1}}


def test_old_snapshot_journal_fields_default_to_none() -> None:
  net = Network()
  net.load(
    {
      "journal": {
        "calls": [
          {"method": "getMe", "token": TOKEN, "kind": "ok", "params": {}},
        ]
      }
    }
  )
  rec = net.journal.calls()[-1]
  assert rec.status is None
  assert rec.response is None


def test_user_and_bot_are_bot_api_users() -> None:
  net = Network()
  user = net.create_user(id=42, first_name="Anna", username="anna")
  bot = net.create_bot(token=TOKEN, first_name="Demo")
  assert user == {"id": 42, "is_bot": False, "first_name": "Anna", "username": "anna"}
  assert bot["is_bot"] is True
  assert bot["id"] == 111111111


def test_private_bot_chat_id_is_the_users_id() -> None:
  net = Network()
  net.create_user(id=42, first_name="Anna")
  net.create_bot(token=TOKEN)
  chat = net.ensure_private_chat(42, 111111111)
  assert chat["id"] == 42
  assert chat["type"] == "private"
  assert chat["first_name"] == "Anna"


async def test_update_queue_acks_like_telegram() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  update_id = net.push_update(TOKEN, {"message": {"text": "/start"}})
  pending = await net.take_updates(TOKEN, None, 0.0)
  assert pending[0]["update_id"] == update_id
  await net.take_updates(TOKEN, update_id + 1, 0.0)
  assert await net.wait_acked(TOKEN, update_id, 0.1) is True


def test_two_people_share_one_thread_with_mirrored_chat_ids() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.open_private_users(1, 2)
  net.append_message(2, {"from": {"id": 1, "is_bot": False, "first_name": "A"}, "text": "hi", "chat": {"id": 2, "type": "private"}})
  # Side A writes into chat id=2 (the peer). The same feed is visible as messages(1) from B's side.
  assert net.messages(2)[0]["text"] == "hi"
  assert net.messages(1)[0]["text"] == "hi"


def test_two_p2p_chats_resolve_by_peer_not_first_match() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_user(id=3, first_name="C")
  net.open_private_users(1, 2)
  net.open_private_users(1, 3)
  net.append_message(2, {"from": {"id": 1, "is_bot": False, "first_name": "A"}, "text": "x", "chat": {"id": 2, "type": "private"}})
  net.append_message(3, {"from": {"id": 1, "is_bot": False, "first_name": "A"}, "text": "y", "chat": {"id": 3, "type": "private"}})
  assert net.messages(2)[0]["text"] == "x"
  assert net.messages(3)[0]["text"] == "y"
  with pytest.raises(ValueError):
    net.messages(1)


def test_viewer_sees_each_peer_as_its_own_chat() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_bot(token=TOKEN, first_name="Demo")
  net.ensure_private_chat(1, 111111111)
  net.open_private_users(1, 2)
  net.append_bot_message(
    TOKEN, 1, {"chat": {"id": 1, "type": "private"}, "from": net.users[111111111], "text": "menu"}
  )
  net.append_message(
    2,
    {
      "from": {"id": 1, "is_bot": False, "first_name": "A"},
      "chat": {"id": 2, "type": "private"},
      "text": "hi",
    },
  )

  chats = {chat["id"]: chat for chat in net.chats_for(1)}
  assert chats[111111111]["first_name"] == "Demo"
  assert chats[111111111]["type"] == "private"
  assert chats[2]["first_name"] == "B"
  assert [m["text"] for m in net.thread_for(1, 111111111)] == ["menu"]
  assert [m["text"] for m in net.thread_for(1, 2)] == ["hi"]
  assert [m["text"] for m in net.thread_for(2, 1)] == ["hi"]


def test_stale_callback_expires_and_answers_false() -> None:
  net = Network()
  net.register_callback("cb-1", 1, 111111111, 1)
  net._callbacks["cb-1"]["date"] -= CALLBACK_TTL + 1
  assert net.answer_callback("cb-1") is False


def test_registering_a_callback_sweeps_stale_ones() -> None:
  net = Network()
  net.register_callback("cb-1", 1, 111111111, 1)
  net._callbacks["cb-1"]["date"] -= CALLBACK_TTL + 1
  net.register_callback("cb-2", 1, 111111111, 2)
  assert list(net._callbacks) == ["cb-2"]


async def test_slow_subscriber_loses_the_oldest_event_not_memory() -> None:
  net = Network()
  queue = net.subscribe()
  for n in range(SSE_QUEUE_LIMIT + 10):
    net.emit({"type": "journal", "record": {"n": n}})
  assert queue.qsize() == SSE_QUEUE_LIMIT
  assert queue.get_nowait()["record"]["n"] == 10
