from __future__ import annotations

from telemulator.chats import (
  RESERVED_OUTBOUND_CHAT_ID,
  allocate_chat_id,
  chat_card,
  next_channel_id,
)
from telemulator.network import Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def test_group_ids_are_minus_one_then_minus_two() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  g1 = net.create_chat(type="group", title="G1", creator_id=1)
  g2 = net.create_chat(type="group", title="G2", creator_id=1)
  assert g1.id == -1
  assert g2.id == -2
  assert g1.type == "group"
  assert g1.title == "G1"
  assert g1.members[1].status == "creator"
  assert g1.last_bot_id is None
  assert g1.privacy_at_join == {}
  assert chat_card(g1) == {"id": -1, "type": "group", "title": "G1"}


def test_supergroup_and_channel_use_minus_100_formula() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  super_chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  channel = net.create_chat(type="channel", title="C", creator_id=1)
  assert super_chat.id == int(f"-100{1:010d}")
  assert channel.id == int(f"-100{2:010d}")
  assert super_chat.id != RESERVED_OUTBOUND_CHAT_ID
  assert channel.type == "channel"


def test_next_channel_id_skips_reserved_outbound() -> None:
  assert int(f"-100{1234567890:010d}") == RESERVED_OUTBOUND_CHAT_ID == -1001234567890
  occupied = {RESERVED_OUTBOUND_CHAT_ID}
  assert next_channel_id(occupied, n=1234567890) == int(f"-100{1234567891:010d}")


def test_dump_without_chats_key_loads_empty() -> None:
  net = Network()
  net.load({"users": [{"id": 1, "is_bot": False, "first_name": "A"}]})
  assert net.chats == {}


def test_dump_load_roundtrip_chat_and_member_fields() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  chat.last_bot_id = 111111111
  chat.privacy_at_join[111111111] = True
  chat.members[1].promoted_by_bot_id = None
  restored = Network()
  restored.load(net.dump())
  got = restored.chats[chat.id]
  assert got.type == "supergroup"
  assert got.title == "S"
  assert got.last_bot_id == 111111111
  assert got.privacy_at_join[111111111] is True
  assert got.members[1].status == "creator"
  assert got.members[1].promoted_by_bot_id is None


def test_thread_for_and_chats_for_include_active_group() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_bot(token=TOKEN, first_name="Demo")
  net.ensure_private_chat(1, 111111111)
  chat = net.create_chat(type="supergroup", title="Team", creator_id=1)
  chat.messages.append({"message_id": 1, "text": "hi", "chat": chat_card(chat)})
  chats = {c["id"]: c for c in net.chats_for(1)}
  assert chats[chat.id]["title"] == "Team"
  assert "first_name" not in chats[chat.id]
  assert chats[111111111]["first_name"] == "Demo"
  assert [m["text"] for m in net.thread_for(1, chat.id)] == ["hi"]
  assert net.chats_for(2) == []
  assert net.thread_for(2, chat.id) == []
