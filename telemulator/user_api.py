from __future__ import annotations

import time
from typing import Any

from telemulator.chats import ACTIVE_STATUSES, ChatRecord, Member, chat_card, member_json
from telemulator.network import Network
from telemulator.privacy import deliver_channel_post_to_bots, deliver_group_message_to_bots


def list_members_json(network: Network, chat: ChatRecord) -> list[dict[str, Any]]:
  return [
    member_json(network, m, chat, include_can_be_edited=False)
    for m in chat.members.values()
    if m.status in ACTIVE_STATUSES
  ]


def actor_can_add(chat: ChatRecord, actor: Member, target_is_bot: bool) -> bool:
  if actor.status not in ACTIVE_STATUSES:
    return False
  if target_is_bot or chat.type == "channel":
    return actor.status in {"creator", "administrator"}
  return True


def message_for_viewer(
  network: Network, stored: dict[str, Any], peer_id: int
) -> dict[str, Any]:
  out = dict(stored)
  chat = network.chats.get(peer_id)
  out["chat"] = chat_card(chat) if chat is not None else network.chat_of(peer_id)
  return out


def _ensure_user(
  network: Network, user_id: int, *, first_name: str = "Test"
) -> dict[str, Any]:
  existing = network.users.get(user_id)
  if existing is None:
    return network.create_user(id=user_id, first_name=first_name)
  return existing


def _token_for_bot(network: Network, bot_id: int) -> str | None:
  for token, runtime in network.bots.items():
    if runtime.user["id"] == bot_id:
      return token
  return None


def _append_inbound(
  network: Network, user_id: int, bot_id: int, fields: dict[str, Any]
) -> dict[str, Any]:
  chat = network.ensure_private_chat(user_id, bot_id)
  thread = network.bot_chats[(user_id, bot_id)]
  message = dict(fields)
  message["message_id"] = max((m.get("message_id", 0) for m in thread), default=0) + 1
  message["date"] = int(time.time())
  message.setdefault("chat", chat)
  thread.append(message)
  return message


def send_text(
  network: Network,
  user_id: int,
  peer_id: int,
  text: str,
  *,
  reply_to_message_id: int | None = None,
) -> int:
  user = _ensure_user(network, user_id)
  chat = network.chats.get(peer_id)
  if chat is not None:
    member = chat.members.get(user_id)
    if member is None or member.status not in ACTIVE_STATUSES:
      raise PermissionError("not a member")
    if chat.type == "channel":
      can_post = member.status == "creator" or (
        member.status == "administrator" and member.can_post_messages
      )
      if not can_post:
        raise PermissionError("no post")
    card = chat_card(chat)
    msg: dict[str, Any] = {"chat": card, "text": text}
    # A channel post is signed by the channel, not a person (docs: channel_post).
    if chat.type == "channel":
      msg["sender_chat"] = card
    else:
      msg["from"] = dict(user)
    if reply_to_message_id is not None:
      origin = next(
        (m for m in chat.messages if m.get("message_id") == reply_to_message_id),
        None,
      )
      if origin is not None:
        msg["reply_to_message"] = dict(origin)
        msg["reply_to_message_id"] = reply_to_message_id
    msg["message_id"] = max((m.get("message_id", 0) for m in chat.messages), default=0) + 1
    msg["date"] = int(time.time())
    chat.messages.append(msg)
    network.emit_chat_message(chat, msg)
    if chat.type == "channel":
      deliver_channel_post_to_bots(network, chat, msg)
    else:
      deliver_group_message_to_bots(network, chat, msg)
    return 0
  if peer_id < 0:
    raise KeyError(peer_id)
  token = _token_for_bot(network, peer_id)
  if token is None:
    if peer_id not in network.users:
      network.create_user(id=peer_id, first_name="Test")
    network.open_private_users(user_id, peer_id)
    network.append_message(
      peer_id,
      {
        "from": dict(user),
        "chat": {"id": peer_id, "type": "private"},
        "text": text,
      },
    )
    return 0
  message = _append_inbound(
    network,
    user_id,
    peer_id,
    {"from": dict(user), "text": text},
  )
  return network.push_update(token, {"message": message})


def _press(
  network: Network, user_id: int, chat_id: int, message_id: int, data: str
) -> tuple[int, str]:
  user = _ensure_user(network, user_id)
  if chat_id in network.chats:
    chat = network.chats[chat_id]
    thread = chat.messages
    found = next((m for m in thread if m.get("message_id") == message_id), None)
    if found is None:
      raise KeyError("message not found")
    buttons = [
      btn
      for row in (found.get("reply_markup") or {}).get("inline_keyboard") or []
      for btn in row
    ]
    if not any(btn.get("callback_data") == data for btn in buttons):
      raise KeyError("button not found")
    from_id = (found.get("from") or {}).get("id")
    if isinstance(from_id, int) and network._is_bot_id(from_id):
      bot_id = from_id
    else:
      bot_id = chat.last_bot_id
    if bot_id is None:
      raise KeyError(f"no bot dialog for chat_id={chat_id}")
    token = _token_for_bot(network, bot_id)
    if token is None:
      raise KeyError(f"no bot dialog for chat_id={chat_id}")
    query_id = network.new_callback_id()
    network.register_callback(query_id, user_id, chat_id, message_id)
    origin = found
    update_id = network.push_update(
      token,
      {
        "callback_query": {
          "id": query_id,
          "from": dict(user),
          "chat_instance": "e2e",
          "data": data,
          "message": {
            "message_id": message_id,
            "date": int(origin.get("date") or time.time()),
            "chat": chat_card(chat),
            "text": origin.get("text") or "",
          },
        }
      },
    )
    return update_id, query_id
  peer_id = chat_id if network._is_bot_id(chat_id) else None
  bot_id = peer_id
  thread: list[dict[str, Any]] = []
  if peer_id is not None:
    thread = network.thread_for(user_id, peer_id)
  else:
    # e2e: chat_id is the person's id, thread is (user_id, bot_id). A person
    # may have several dialogs, so look up the thread with this message_id, not the first one.
    candidates = [
      (owner_id, messages)
      for (cid, owner_id), messages in network.bot_chats.items()
      if cid == chat_id
    ]
    for owner_id, messages in candidates:
      if any(m.get("message_id") == message_id for m in messages):
        thread, bot_id, peer_id = messages, owner_id, owner_id
        break
    else:
      if candidates:
        bot_id, thread = candidates[0][0], candidates[0][1]
        peer_id = bot_id
  if bot_id is None:
    raise KeyError(f"no bot dialog for chat_id={chat_id}")
  found = next((m for m in thread if m.get("message_id") == message_id), None)
  if found is None:
    raise KeyError("message not found")
  buttons = [
    btn
    for row in (found.get("reply_markup") or {}).get("inline_keyboard") or []
    for btn in row
  ]
  if not any(btn.get("callback_data") == data for btn in buttons):
    raise KeyError("button not found")
  token = _token_for_bot(network, bot_id)
  if token is None:
    raise KeyError(f"no bot dialog for chat_id={chat_id}")
  query_id = network.new_callback_id()
  network.register_callback(query_id, user_id, bot_id, message_id)
  origin = found
  update_id = network.push_update(
    token,
    {
      "callback_query": {
        "id": query_id,
        "from": dict(user),
        "chat_instance": "e2e",
        "data": data,
        "message": {
          "message_id": message_id,
          "date": int(origin.get("date") or time.time()),
          "chat": {"id": user_id, "type": "private"},
          "text": origin.get("text") or "",
        },
      }
    },
  )
  return update_id, query_id


def press_callback(
  network: Network, user_id: int, chat_id: int, message_id: int, data: str
) -> int:
  update_id, _query_id = _press(network, user_id, chat_id, message_id, data)
  return update_id


def send_document(
  network: Network,
  user_id: int,
  peer_id: int,
  *,
  file_id: str = "user-doc-1",
  file_name: str = "certificate.pdf",
) -> int:
  user = _ensure_user(network, user_id)
  token = _token_for_bot(network, peer_id)
  if token is None:
    raise KeyError(peer_id)
  network.files[f"{file_id}.bin"] = b"e2e-file-content"
  message = _append_inbound(
    network,
    user_id,
    peer_id,
    {
      "from": dict(user),
      "document": {
        "file_id": file_id,
        "file_unique_id": file_id,
        "file_name": file_name,
        "file_size": 17,
      },
    },
  )
  return network.push_update(token, {"message": message})


def send_photo(
  network: Network,
  user_id: int,
  peer_id: int,
  *,
  file_id: str = "user-photo-1",
) -> int:
  user = _ensure_user(network, user_id)
  token = _token_for_bot(network, peer_id)
  if token is None:
    raise KeyError(peer_id)
  network.files[f"{file_id}.bin"] = b"e2e-photo-content"
  message = _append_inbound(
    network,
    user_id,
    peer_id,
    {
      "from": dict(user),
      # A ladder of sizes, as real Telegram sends: a consumer picking the
      # largest takes the last entry. One size would not exercise that.
      "photo": [
        {"file_id": f"{file_id}-s", "file_unique_id": f"{file_id}-s",
         "width": 90, "height": 90, "file_size": 17},
        {"file_id": file_id, "file_unique_id": file_id,
         "width": 1280, "height": 1280, "file_size": 17},
      ],
    },
  )
  return network.push_update(token, {"message": message})
