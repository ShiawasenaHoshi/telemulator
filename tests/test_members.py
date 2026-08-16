from __future__ import annotations

from telemulator.chats import member_json
from telemulator.network import Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


def _seed(net: Network) -> None:
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_bot(token=TOKEN, first_name="Club", username="clubbot")
  net.create_bot(token=ALERT, first_name="Alert", username="alertbot")


def test_add_human_is_member_and_writes_service_in_supergroup() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  member = net.add_member(chat.id, 2, actor_id=1)
  assert member.status == "member"
  assert chat.messages[0]["new_chat_members"][0]["id"] == 2
  assert "text" not in chat.messages[0]
  assert chat.messages[0]["from"]["id"] == 1
  assert chat.messages[0]["chat"] == {"id": chat.id, "type": "supergroup", "title": "S"}


def test_channel_add_does_not_write_service_message() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="channel", title="C", creator_id=1)
  net.add_member(chat.id, 2, actor_id=1)
  assert chat.messages == []
  assert chat.members[2].status == "member"


def test_add_bot_to_group_defaults_member_and_snapshots_privacy() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="group", title="G", creator_id=1)
  member = net.add_member(chat.id, 111111111, actor_id=1)
  assert member.status == "member"
  assert chat.privacy_at_join[111111111] is True


def test_add_bot_administrator_without_flags_is_not_demote() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  member = net.add_member(chat.id, 111111111, actor_id=1, status="administrator")
  assert member.status == "administrator"
  assert member.can_post_messages is False
  assert member.can_manage_chat is False
  assert member.promoted_by_bot_id is None
  body = member_json(net, member, chat, caller_bot_id=111111111)
  assert body["status"] == "administrator"
  assert body["is_anonymous"] is False
  assert body["can_be_edited"] is False
  for key in (
    "can_manage_chat",
    "can_delete_messages",
    "can_manage_video_chats",
    "can_restrict_members",
    "can_promote_members",
    "can_change_info",
    "can_invite_users",
    "can_post_stories",
    "can_edit_stories",
    "can_delete_stories",
  ):
    assert key in body
  assert "can_pin_messages" in body
  assert "can_manage_topics" in body
  assert "can_post_messages" not in body


def test_add_bot_to_channel_is_admin_with_post() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="channel", title="C", creator_id=1)
  member = net.add_member(chat.id, 111111111, actor_id=1)
  assert member.status == "administrator"
  assert member.can_post_messages is True
  assert member.can_manage_chat is True
  assert member.can_delete_messages is False
  body = member_json(net, member, chat, caller_bot_id=111111111)
  assert body["can_post_messages"] is True
  assert "can_edit_messages" in body
  assert "can_manage_direct_messages" in body
  assert "can_pin_messages" not in body
  assert "can_manage_tags" not in body


def test_implied_manage_chat_when_any_flag_true() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  member = net.add_member(
    chat.id, 111111111, actor_id=1, status="administrator", flags={"can_delete_messages": True}
  )
  assert member.can_manage_chat is True


def test_remove_self_is_left_and_service_has_left_chat_member() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="supergroup", title="S", creator_id=1, member_ids=[2])
  left = net.remove_member(chat.id, 2, actor_id=2)
  assert left.status == "left"
  assert chat.messages[-1]["left_chat_member"]["id"] == 2
  assert isinstance(chat.messages[-1]["left_chat_member"], dict)


def test_remove_other_in_group_is_left_in_super_is_kicked() -> None:
  net = Network()
  _seed(net)
  basic = net.create_chat(type="group", title="G", creator_id=1, member_ids=[2])
  super_chat = net.create_chat(type="supergroup", title="S", creator_id=1, member_ids=[2])
  assert net.remove_member(basic.id, 2, actor_id=1).status == "left"
  kicked = net.remove_member(super_chat.id, 2, actor_id=1)
  assert kicked.status == "kicked"
  assert kicked.until_date == 0


def test_members_option_wins_over_member_ids() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(
    type="supergroup",
    title="S",
    creator_id=1,
    member_ids=[2],
    members=[{"user_id": 111111111, "status": "administrator"}],
  )
  assert 2 not in chat.members
  assert chat.members[111111111].status == "administrator"


def test_user_api_member_json_omits_can_be_edited() -> None:
  net = Network()
  _seed(net)
  chat = net.create_chat(type="supergroup", title="S", creator_id=1)
  owner = member_json(
    net, chat.members[1], chat, include_can_be_edited=False
  )
  assert owner == {
    "status": "creator",
    "user": net.users[1],
    "is_anonymous": False,
  }
