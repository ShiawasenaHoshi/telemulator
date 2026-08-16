from __future__ import annotations

from typing import Any, Protocol

from telemulator.chats import ACTIVE_STATUSES, ChatRecord, Member


class _Network(Protocol):
  bots: dict[str, Any]
  users: dict[int, dict[str, Any]]

  def push_update(self, token: str, payload: dict[str, Any]) -> int: ...


def _command_address(text: str) -> tuple[bool, str | None]:
  if not text.startswith("/"):
    return False, None
  head = text.split(None, 1)[0]
  body = head[1:]
  if "@" in body:
    _cmd, _, username = body.partition("@")
    return True, username.lower()
  return True, None


def _is_admin_member(member: Member) -> bool:
  return member.status in {"creator", "administrator"}


def deliver_channel_post_to_bots(
  network: _Network,
  chat: ChatRecord,
  message: dict[str, Any],
  *,
  key: str = "channel_post",
  skip_token: str | None = None,
) -> None:
  """Privacy does not apply to channels: every member bot sees the post."""
  for token, runtime in network.bots.items():
    if token == skip_token:
      continue
    member = chat.members.get(runtime.user["id"])
    if member is None or member.status not in ACTIVE_STATUSES:
      continue
    network.push_update(token, {key: dict(message)})


def deliver_group_message_to_bots(
  network: _Network, chat: ChatRecord, message: dict[str, Any]
) -> None:
  from_user = message.get("from") or {}
  if from_user.get("is_bot"):
    return
  is_service = "new_chat_members" in message or "left_chat_member" in message
  text = str(message.get("text") or "")
  is_cmd, addressed = _command_address(text)
  reply_bot_id = None
  origin = message.get("reply_to_message")
  if origin is None and message.get("reply_to_message_id") is not None:
    origin = next(
      (m for m in chat.messages if m.get("message_id") == message["reply_to_message_id"]),
      None,
    )
  if isinstance(origin, dict):
    rid = (origin.get("from") or {}).get("id")
    if isinstance(rid, int) and network.users.get(rid, {}).get("is_bot"):
      reply_bot_id = rid
  step2: list[str] = []
  step3: list[tuple[str, int]] = []
  for token, runtime in network.bots.items():
    bot_id = runtime.user["id"]
    member = chat.members.get(bot_id)
    if member is None or member.status not in ACTIVE_STATUSES:
      continue
    if is_service:
      network.push_update(token, {"message": message})
      continue
    privacy_on = chat.privacy_at_join.get(bot_id, True)
    if _is_admin_member(member) or privacy_on is False:
      step2.append(token)
      continue
    sees = False
    if reply_bot_id == bot_id:
      sees = True
    elif is_cmd and addressed is not None:
      uname = str(runtime.user.get("username") or "").lower()
      if uname and addressed == uname:
        sees = True
    elif is_cmd and addressed is None and chat.last_bot_id == bot_id:
      sees = True
    if sees:
      step3.append((token, bot_id))
  if reply_bot_id is not None:
    step3 = [(t, b) for t, b in step3 if b == reply_bot_id]
  elif is_cmd and addressed is not None:
    step3 = [
      (t, b)
      for t, b in step3
      if str(network.bots[t].user.get("username") or "").lower() == addressed
    ]
  elif is_cmd and addressed is None:
    step3 = [(t, b) for t, b in step3 if b == chat.last_bot_id]
  else:
    step3 = []
  winners = set(step2)
  if step3:
    winners.add(step3[0][0])
  for token in winners:
    network.push_update(token, {"message": message})
