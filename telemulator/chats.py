from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RESERVED_OUTBOUND_CHAT_ID = -1001234567890
ACTIVE_STATUSES = frozenset({"creator", "administrator", "member"})
CHAT_TYPES = frozenset({"group", "supergroup", "channel"})
ADMIN_FLAGS = (
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
  "can_post_messages",
  "can_edit_messages",
  "can_manage_direct_messages",
  "can_pin_messages",
  "can_manage_topics",
)


@dataclass
class Member:
  user_id: int
  status: str
  until_date: int = 0
  promoted_by_bot_id: int | None = None
  can_manage_chat: bool = False
  can_delete_messages: bool = False
  can_manage_video_chats: bool = False
  can_restrict_members: bool = False
  can_promote_members: bool = False
  can_change_info: bool = False
  can_invite_users: bool = False
  can_post_stories: bool = False
  can_edit_stories: bool = False
  can_delete_stories: bool = False
  can_post_messages: bool = False
  can_edit_messages: bool = False
  can_manage_direct_messages: bool = False
  can_pin_messages: bool = False
  can_manage_topics: bool = False


@dataclass
class ChatRecord:
  id: int
  type: str
  title: str
  members: dict[int, Member] = field(default_factory=dict)
  messages: list[dict[str, Any]] = field(default_factory=list)
  last_bot_id: int | None = None
  privacy_at_join: dict[int, bool] = field(default_factory=dict)


REQUIRED_ADMIN_JSON = (
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
)


def implied_manage_chat(member: Member) -> None:
  for name in ADMIN_FLAGS:
    if name == "can_manage_chat":
      continue
    if getattr(member, name):
      member.can_manage_chat = True
      return


def member_json(
  network: Any,
  member: Member,
  chat: ChatRecord,
  *,
  caller_bot_id: int | None = None,
  include_can_be_edited: bool = True,
) -> dict[str, Any]:
  user = dict(network.users[member.user_id])
  if member.status == "creator":
    return {"status": "creator", "user": user, "is_anonymous": False}
  if member.status == "member":
    return {"status": "member", "user": user}
  if member.status == "left":
    return {"status": "left", "user": user}
  if member.status == "kicked":
    return {"status": "kicked", "user": user, "until_date": member.until_date}
  body: dict[str, Any] = {
    "status": "administrator",
    "user": user,
    "is_anonymous": False,
  }
  if include_can_be_edited:
    body["can_be_edited"] = (
      caller_bot_id is not None and member.promoted_by_bot_id == caller_bot_id
    )
  for name in REQUIRED_ADMIN_JSON:
    body[name] = getattr(member, name)
  if chat.type == "channel":
    body["can_post_messages"] = member.can_post_messages
    body["can_edit_messages"] = member.can_edit_messages
    body["can_manage_direct_messages"] = member.can_manage_direct_messages
  if chat.type in {"group", "supergroup"}:
    body["can_pin_messages"] = member.can_pin_messages
  if chat.type == "supergroup":
    body["can_manage_topics"] = member.can_manage_topics
  return body


def chat_card(chat: ChatRecord) -> dict[str, Any]:
  return {"id": chat.id, "type": chat.type, "title": chat.title}


def next_channel_id(occupied: set[int], *, n: int = 1) -> int:
  while True:
    cid = int(f"-100{n:010d}")
    if cid != RESERVED_OUTBOUND_CHAT_ID and cid not in occupied:
      return cid
    n += 1


def allocate_chat_id(existing: dict[int, ChatRecord], chat_type: str) -> int:
  if chat_type == "group":
    n = 1
    while -n in existing:
      n += 1
    return -n
  return next_channel_id(set(existing), n=1)


def member_dump(member: Member) -> dict[str, Any]:
  data: dict[str, Any] = {
    "user_id": member.user_id,
    "status": member.status,
    "until_date": member.until_date,
    "promoted_by_bot_id": member.promoted_by_bot_id,
  }
  for name in ADMIN_FLAGS:
    data[name] = getattr(member, name)
  return data


def member_load(data: dict[str, Any]) -> Member:
  kwargs: dict[str, Any] = {
    "user_id": int(data["user_id"]),
    "status": str(data["status"]),
    "until_date": int(data.get("until_date") or 0),
    "promoted_by_bot_id": (
      None if data.get("promoted_by_bot_id") is None else int(data["promoted_by_bot_id"])
    ),
  }
  for name in ADMIN_FLAGS:
    kwargs[name] = bool(data.get(name, False))
  return Member(**kwargs)


def chat_dump(chat: ChatRecord) -> dict[str, Any]:
  return {
    "id": chat.id,
    "type": chat.type,
    "title": chat.title,
    "members": [member_dump(m) for m in chat.members.values()],
    "messages": list(chat.messages),
    "last_bot_id": chat.last_bot_id,
    "privacy_at_join": {str(k): v for k, v in chat.privacy_at_join.items()},
  }


def chat_load(data: dict[str, Any]) -> ChatRecord:
  members = [member_load(item) for item in data.get("members", [])]
  return ChatRecord(
    id=int(data["id"]),
    type=str(data["type"]),
    title=str(data["title"]),
    members={m.user_id: m for m in members},
    messages=list(data.get("messages", [])),
    last_bot_id=(None if data.get("last_bot_id") is None else int(data["last_bot_id"])),
    privacy_at_join={
      int(k): bool(v) for k, v in data.get("privacy_at_join", {}).items()
    },
  )
