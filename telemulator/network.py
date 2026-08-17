from __future__ import annotations

import asyncio
import base64
import json
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from telemulator.chats import (
  ACTIVE_STATUSES,
  ADMIN_FLAGS,
  CHAT_TYPES,
  ChatRecord,
  Member,
  allocate_chat_id,
  chat_card,
  chat_dump,
  chat_load,
  implied_manage_chat,
  member_json,
)
from telemulator.journal import Journal, journal_item

# Telegram expires a callback query after about 15 minutes. Without a TTL the
# records pile up forever: a press the bot never answered stays forever — in
# memory and in every snapshot.
CALLBACK_TTL = 900
# A backgrounded tab reads SSE slowly; better to drop an old event than grow
# without a ceiling.
SSE_QUEUE_LIMIT = 1000

EXCLUDED_BY_DEFAULT = frozenset(
  {"chat_member", "message_reaction", "message_reaction_count"}
)


@dataclass
class BotRuntime:
  user: dict[str, Any]
  privacy_mode: bool = True
  updates: list[dict[str, Any]] = field(default_factory=list)
  # Offset N+1 in getUpdates means the handler for update N has returned
  # control: with handle_as_tasks=False aiogram issues the next request
  # only after `await handle_update`. That is the only precise boundary
  # of "the bot has finished" — it sent every message and wrote everything to the DB.
  acked: int = 0
  errors: dict[int, tuple[int, dict[str, Any]]] = field(default_factory=dict)
  webhook_url: str = ""
  last_error_date: int | None = None
  last_error_message: str | None = None
  pending_getupdates: asyncio.Task[Any] | None = None
  preempted: set[asyncio.Task[Any]] = field(default_factory=set)
  allowed_updates: list[str] | None = None
  # Changing the subscription does not swallow what is already queued: ids below cutoff stay visible.
  allowed_updates_cutoff: int = 0
  _next_update_id: int = 1
  _arrived: asyncio.Event = field(default_factory=asyncio.Event)
  _acked: asyncio.Event = field(default_factory=asyncio.Event)


def _update_kind(update: dict[str, Any]) -> str:
  return next(k for k in update if k != "update_id")


def _kind_allowed(kind: str, subscription: list[str] | None) -> bool:
  if subscription is None:
    return kind not in EXCLUDED_BY_DEFAULT
  return kind in subscription


def update_is_visible(runtime: BotRuntime, update: dict[str, Any]) -> bool:
  if update["update_id"] < runtime.allowed_updates_cutoff:
    return True
  return _kind_allowed(_update_kind(update), runtime.allowed_updates)


def apply_allowed_updates(runtime: BotRuntime, params: dict[str, Any]) -> None:
  if "allowed_updates" not in params:
    return
  raw = params["allowed_updates"]
  parsed = json.loads(str(raw)) if not isinstance(raw, list) else raw
  updated = None if parsed == [] else [str(x) for x in parsed]
  # The client sends allowed_updates on every poll. Moving the cutoff without a
  # subscription change is not allowed: already queued updates of other types would become visible.
  if updated == runtime.allowed_updates:
    return
  runtime.allowed_updates_cutoff = runtime._next_update_id
  runtime.allowed_updates = updated


def _user_dict(
  *, id: int, first_name: str, username: str | None, is_bot: bool
) -> dict[str, Any]:
  user: dict[str, Any] = {"id": id, "is_bot": is_bot, "first_name": first_name}
  if username is not None:
    user["username"] = username
  return user


def _chat_of(user: dict[str, Any]) -> dict[str, Any]:
  chat: dict[str, Any] = {
    "id": user["id"],
    "type": "private",
    "first_name": user["first_name"],
  }
  if "username" in user:
    chat["username"] = user["username"]
  if "last_name" in user:
    chat["last_name"] = user["last_name"]
  return chat


def _all_can_false(member: Member) -> bool:
  return all(not getattr(member, name) for name in ADMIN_FLAGS)


def notify_membership(
  network: Network, chat: ChatRecord, actor_id: int, old: Member, new: Member
) -> None:
  date = int(time.time())
  actor = dict(network.users[actor_id])

  def payload(caller_bot_id: int) -> dict[str, Any]:
    return {
      "chat": chat_card(chat),
      "from": actor,
      "date": date,
      "old_chat_member": member_json(
        network, old, chat, caller_bot_id=caller_bot_id
      ),
      "new_chat_member": member_json(
        network, new, chat, caller_bot_id=caller_bot_id
      ),
    }

  affected = network._token_for_bot_id(new.user_id)
  if affected is not None:
    network.push_update(
      affected, {"my_chat_member": payload(network.bots[affected].user["id"])}
    )
  for token, runtime in network.bots.items():
    if token == affected:
      continue
    other = chat.members.get(runtime.user["id"])
    if other is None or other.status not in {"creator", "administrator"}:
      continue
    network.push_update(token, {"chat_member": payload(runtime.user["id"])})


class Network:
  def __init__(self) -> None:
    self.users: dict[int, dict[str, Any]] = {}
    self.bots: dict[str, BotRuntime] = {}
    self.private_user_chats: dict[frozenset[int], list[dict[str, Any]]] = {}
    self.bot_chats: dict[tuple[int, int], list[dict[str, Any]]] = {}
    self.chats: dict[int, ChatRecord] = {}
    self.journal = Journal()
    self.files: dict[str, bytes] = {}
    self._reply_keyboards: dict[tuple[int, int], list[list[str]] | None] = {}
    self._webhook_tasks: set[asyncio.Task[Any]] = set()
    self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
    self._sessions: dict[str, int] = {}
    self._next_user_id = 1
    self._next_callback_id = 1
    self._callbacks: dict[str, dict[str, int]] = {}

  def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SSE_QUEUE_LIMIT)
    self._subscribers.append(queue)
    return queue

  def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
    if queue in self._subscribers:
      self._subscribers.remove(queue)

  def emit(self, event: dict[str, Any]) -> None:
    for queue in list(self._subscribers):
      if queue.full():
        queue.get_nowait()
      queue.put_nowait(event)

  def emit_chat_message(
    self, chat: ChatRecord, msg: dict[str, Any], event_type: str = "message"
  ) -> None:
    card = chat_card(chat)
    for member in chat.members.values():
      if member.status not in ACTIVE_STATUSES:
        continue
      self.emit(
        {
          "type": event_type,
          "peer_id": chat.id,
          "viewer_id": member.user_id,
          "message": {**dict(msg), "chat": card},
        }
      )

  def create_session(self, user_id: int) -> str:
    for token, existing_user_id in self._sessions.items():
      if existing_user_id == user_id:
        return token
    token = "usr_" + secrets.token_urlsafe(16)
    self._sessions[token] = user_id
    return token

  def user_id_for_session(self, token: str) -> int | None:
    return self._sessions.get(token)

  def new_callback_id(self) -> str:
    query_id = f"cb-{self._next_callback_id}"
    self._next_callback_id += 1
    return query_id

  def _sweep_callbacks(self) -> None:
    edge = int(time.time()) - CALLBACK_TTL
    for query_id in [q for q, rec in self._callbacks.items() if rec["date"] < edge]:
      del self._callbacks[query_id]

  def register_callback(
    self, query_id: str, user_id: int, peer_id: int, message_id: int
  ) -> None:
    self._sweep_callbacks()
    self._callbacks[query_id] = {
      "user_id": user_id,
      "peer_id": peer_id,
      "message_id": message_id,
      "date": int(time.time()),
    }

  def answer_callback(self, query_id: str) -> bool:
    self._sweep_callbacks()
    if query_id not in self._callbacks:
      return False
    del self._callbacks[query_id]
    self.emit({"type": "callback_answered", "query_id": query_id})
    return True

  def create_user(
    self,
    *,
    id: int | None = None,
    first_name: str,
    username: str | None = None,
    is_bot: bool = False,
  ) -> dict[str, Any]:
    if id is None:
      while self._next_user_id in self.users:
        self._next_user_id += 1
      id = self._next_user_id
      self._next_user_id += 1
    user = _user_dict(id=id, first_name=first_name, username=username, is_bot=is_bot)
    self.users[id] = user
    return user

  def create_bot(
    self,
    *,
    token: str,
    first_name: str = "Demo",
    username: str | None = None,
  ) -> dict[str, Any]:
    bot_id = int(token.split(":")[0])
    username = username or f"telemulator{bot_id}"
    user = _user_dict(id=bot_id, first_name=first_name, username=username, is_bot=True)
    self.users[bot_id] = user
    self.bots[token] = BotRuntime(user=user, privacy_mode=True)
    return user

  def reset(self) -> Network:
    """Empty network, but update numbering continues.

    A bot in compose survives /admin/reset and keeps its offset in memory. If ids
    restart from 1, it will never pick them up: getUpdates(offset=N) returns empty,
    and the tg_updates dedup in Postgres treats them as old. Defect #33.
    """
    fresh = Network()
    for token, runtime in self.bots.items():
      fresh.create_bot(
        token=token,
        first_name=runtime.user["first_name"],
        username=runtime.user.get("username"),
      )
      fresh.bots[token]._next_update_id = runtime._next_update_id
      fresh.bots[token].privacy_mode = runtime.privacy_mode
      fresh.bots[token].allowed_updates = (
        None if runtime.allowed_updates is None else list(runtime.allowed_updates)
      )
      fresh.bots[token].allowed_updates_cutoff = runtime.allowed_updates_cutoff
    # An SSE subscriber is an open browser connection, not network state.
    # Without carrying it over the tab stays alive but goes mute forever:
    # events drain into the dead network's queue. Sessions do not survive
    # reset — the people are gone; the client learns that from the reset
    # event and creates a person again.
    fresh._subscribers = self._subscribers
    self._subscribers = []
    fresh.emit({"type": "reset"})
    return fresh

  def bot_by_token(self, token: str) -> dict[str, Any] | None:
    runtime = self.bots.get(token)
    return None if runtime is None else runtime.user

  def chat_of(self, user_id: int) -> dict[str, Any]:
    return _chat_of(self.users[user_id])

  def ensure_private_chat(self, user_id: int, bot_id: int) -> dict[str, Any]:
    self.bot_chats.setdefault((user_id, bot_id), [])
    return _chat_of(self.users[user_id])

  def ensure_outbound_chat(self, chat_id: int, bot_id: int) -> None:
    self.bot_chats.setdefault((chat_id, bot_id), [])

  def open_private_users(self, a_id: int, b_id: int) -> dict[str, Any]:
    self.private_user_chats.setdefault(frozenset({a_id, b_id}), [])
    return _chat_of(self.users[b_id])

  def create_chat(
    self,
    *,
    type: str,
    title: str,
    creator_id: int,
    member_ids: list[int] | None = None,
    members: list[dict[str, Any]] | None = None,
  ) -> ChatRecord:
    if type not in CHAT_TYPES:
      raise ValueError(type)
    if creator_id not in self.users:
      raise KeyError(creator_id)
    chat_id = allocate_chat_id(self.chats, type)
    record = ChatRecord(id=chat_id, type=type, title=title)
    record.members[creator_id] = Member(user_id=creator_id, status="creator")
    self.chats[chat_id] = record
    specs: list[dict[str, Any]]
    if members is not None:
      specs = members
    else:
      specs = [{"user_id": uid} for uid in (member_ids or [])]
    for spec in specs:
      member_id = int(spec["user_id"])
      # The creator is already in members; a second add would wipe their creator status.
      if member_id in record.members:
        continue
      flags = {k: bool(v) for k, v in spec.items() if k.startswith("can_")}
      self.add_member(
        record.id,
        member_id,
        actor_id=creator_id,
        status=spec.get("status"),
        flags=flags or None,
      )
    return record

  def _token_for_bot_id(self, bot_id: int) -> str | None:
    for token, runtime in self.bots.items():
      if runtime.user["id"] == bot_id:
        return token
    return None

  def _append_service(
    self, chat: ChatRecord, actor_id: int, field: str, payload: Any
  ) -> None:
    if chat.type == "channel":
      return
    msg: dict[str, Any] = {
      "message_id": max((m.get("message_id", 0) for m in chat.messages), default=0) + 1,
      "date": int(time.time()),
      "chat": chat_card(chat),
      "from": dict(self.users[actor_id]),
      field: payload,
    }
    chat.messages.append(msg)
    self.emit_chat_message(chat, msg)
    from telemulator.privacy import deliver_group_message_to_bots
    deliver_group_message_to_bots(self, chat, msg)

  def add_member(
    self,
    chat_id: int,
    user_id: int,
    *,
    actor_id: int,
    status: str | None = None,
    flags: dict[str, bool] | None = None,
  ) -> Member:
    chat = self.chats[chat_id]
    if user_id not in self.users:
      raise KeyError(user_id)
    existing = chat.members.get(user_id)
    if existing is not None and existing.status == "kicked":
      raise PermissionError("kicked")
    if existing is not None and existing.status in ACTIVE_STATUSES:
      raise PermissionError("already a member")
    old = existing if existing is not None else Member(user_id=user_id, status="left")
    user = self.users[user_id]
    is_bot = bool(user.get("is_bot"))
    flags = dict(flags or {})
    if is_bot and chat.type == "channel":
      member = Member(user_id=user_id, status="administrator")
      for name, value in flags.items():
        if hasattr(member, name):
          setattr(member, name, value)
      if "can_post_messages" not in flags:
        member.can_post_messages = True
    elif status == "administrator":
      member = Member(user_id=user_id, status="administrator")
      for name, value in flags.items():
        if hasattr(member, name):
          setattr(member, name, value)
    else:
      member = Member(user_id=user_id, status="member")
    implied_manage_chat(member)
    member.promoted_by_bot_id = None
    chat.members[user_id] = member
    if is_bot:
      token = self._token_for_bot_id(user_id)
      privacy = True
      if token is not None:
        privacy = bool(getattr(self.bots[token], "privacy_mode", True))
      chat.privacy_at_join[user_id] = privacy
    notify_membership(self, chat, actor_id, old, member)
    self._append_service(chat, actor_id, "new_chat_members", [dict(user)])
    return member

  def remove_member(self, chat_id: int, user_id: int, *, actor_id: int) -> Member:
    chat = self.chats[chat_id]
    member = chat.members[user_id]
    old = replace(member)
    if user_id == actor_id:
      member.status = "left"
      member.until_date = 0
    elif chat.type == "group":
      member.status = "left"
      member.until_date = 0
    else:
      member.status = "kicked"
      member.until_date = 0
    notify_membership(self, chat, actor_id, old, member)
    self._append_service(chat, actor_id, "left_chat_member", dict(self.users[user_id]))
    return member

  def leave_chat(self, chat_id: int, user_id: int) -> Member:
    return self.remove_member(chat_id, user_id, actor_id=user_id)

  def ban_member(self, chat_id: int, user_id: int, *, actor_id: int) -> Member:
    chat = self.chats[chat_id]
    member = chat.members[user_id]
    old = replace(member)
    if chat.type == "group":
      member.status = "left"
    else:
      member.status = "kicked"
    member.until_date = 0
    notify_membership(self, chat, actor_id, old, member)
    self._append_service(chat, actor_id, "left_chat_member", dict(self.users[user_id]))
    return member

  def unban_member(
    self,
    chat_id: int,
    user_id: int,
    *,
    actor_id: int,
    only_if_banned: bool = False,
  ) -> Member:
    chat = self.chats[chat_id]
    member = chat.members[user_id]
    if member.status == "kicked":
      old = replace(member)
      member.status = "left"
      member.until_date = 0
      notify_membership(self, chat, actor_id, old, member)
      return member
    if only_if_banned or member.status not in ACTIVE_STATUSES:
      return member
    old = replace(member)
    member.status = "left"
    member.until_date = 0
    notify_membership(self, chat, actor_id, old, member)
    self._append_service(chat, actor_id, "left_chat_member", dict(self.users[user_id]))
    return member

  def promote_member(
    self,
    chat_id: int,
    user_id: int,
    *,
    actor_id: int,
    flags: dict[str, bool],
  ) -> Member:
    chat = self.chats[chat_id]
    member = chat.members[user_id]
    old = replace(member)
    already_admin = member.status == "administrator"
    for name in ADMIN_FLAGS:
      setattr(member, name, bool(flags.get(name, False)))
    implied_manage_chat(member)
    if already_admin and _all_can_false(member):
      is_bot = bool(self.users[user_id].get("is_bot"))
      member.status = "left" if is_bot and chat.type == "channel" else "member"
      member.promoted_by_bot_id = None
    else:
      member.status = "administrator"
      member.promoted_by_bot_id = actor_id
    notify_membership(self, chat, actor_id, old, member)
    return member

  def patch_member(
    self,
    chat_id: int,
    user_id: int,
    *,
    actor_id: int,
    status: str,
    flags: dict[str, bool] | None = None,
    require_creator: bool = True,
  ) -> Member:
    chat = self.chats[chat_id]
    actor = chat.members.get(actor_id)
    if actor is None or actor.status not in ACTIVE_STATUSES:
      raise PermissionError("not a member")
    if require_creator and actor.status != "creator":
      raise PermissionError("not creator")
    target = chat.members.get(user_id)
    if target is None:
      raise KeyError(user_id)
    if target.status == "creator":
      raise ValueError("owner")
    old = replace(target)
    flags = dict(flags or {})
    already_admin = target.status == "administrator"
    if status == "member":
      if already_admin:
        if chat.type == "channel" and self.users[user_id].get("is_bot"):
          target.status = "left"
        else:
          target.status = "member"
        for name in ADMIN_FLAGS:
          setattr(target, name, False)
        target.promoted_by_bot_id = None
    elif status == "administrator":
      target.status = "administrator"
      # A person granted the rights via User/Admin API — do not give the bot can_be_edited.
      target.promoted_by_bot_id = None
      for name in ADMIN_FLAGS:
        setattr(target, name, bool(flags.get(name, False)))
      implied_manage_chat(target)
      if already_admin and not any(getattr(target, name) for name in ADMIN_FLAGS):
        if chat.type == "channel" and self.users[user_id].get("is_bot"):
          target.status = "left"
        else:
          target.status = "member"
        target.promoted_by_bot_id = None
    notify_membership(self, chat, actor_id, old, target)
    return target

  def _find_thread(self, chat_id: int) -> list[dict[str, Any]] | None:
    matches: list[list[dict[str, Any]]] = [
      thread for peers, thread in self.private_user_chats.items() if chat_id in peers
    ]
    matches.extend(
      thread
      for (user_id, _bot_id), thread in self.bot_chats.items()
      if chat_id == user_id
    )
    if len(matches) > 1:
      raise ValueError(f"chat_id {chat_id} matches several private threads")
    return matches[0] if matches else None

  def append_message(self, chat_id: int, message: dict[str, Any]) -> dict[str, Any]:
    from_id = (message.get("from") or {}).get("id")
    peer_id = (message.get("chat") or {}).get("id", chat_id)
    thread: list[dict[str, Any]] | None = None
    if from_id is not None:
      thread = self.private_user_chats.get(frozenset({from_id, peer_id}))
      if thread is None:
        thread = self.bot_chats.get((chat_id, from_id))
    if thread is None:
      bot_matches = [
        t for (user_id, _bot_id), t in self.bot_chats.items() if user_id == chat_id
      ]
      if len(bot_matches) == 1:
        thread = bot_matches[0]
    if thread is None:
      thread = self._find_thread(chat_id)
    if thread is None:
      raise KeyError(chat_id)
    msg = dict(message)
    msg["message_id"] = max((m.get("message_id", 0) for m in thread), default=0) + 1
    msg["date"] = int(time.time())
    thread.append(msg)
    self._emit_message(msg, from_id=from_id, peer_id=peer_id)
    return msg

  def _emit_message(
    self,
    msg: dict[str, Any],
    *,
    from_id: int | None,
    peer_id: int,
    event_type: str = "message",
  ) -> None:
    peers = [pid for pid in (from_id, peer_id) if isinstance(pid, int)]
    for viewer_id in peers:
      other = peer_id if viewer_id == from_id else from_id
      if other is None:
        continue
      self.emit(
        {
          "type": event_type,
          "peer_id": other,
          "viewer_id": viewer_id,
          "message": {
            **dict(msg),
            "chat": _chat_of(self.users[other]),
          },
        }
      )

  def messages(self, chat_id: int) -> list[dict[str, Any]]:
    thread = self._find_thread(chat_id)
    return thread if thread is not None else []

  def messages_for_peer(self, a_id: int, b_id: int) -> list[dict[str, Any]]:
    return list(self.private_user_chats.get(frozenset({a_id, b_id}), []))

  def _is_bot_id(self, peer_id: int) -> bool:
    if any(runtime.user["id"] == peer_id for runtime in self.bots.values()):
      return True
    user = self.users.get(peer_id)
    return bool(user and user.get("is_bot"))

  def thread_for(self, viewer_id: int, peer_id: int) -> list[dict[str, Any]]:
    chat = self.chats.get(peer_id)
    if chat is not None:
      member = chat.members.get(viewer_id)
      if member is None or member.status not in ACTIVE_STATUSES:
        return []
      return list(chat.messages)
    if self._is_bot_id(peer_id):
      return list(self.bot_chats.get((viewer_id, peer_id), []))
    return list(self.private_user_chats.get(frozenset({viewer_id, peer_id}), []))

  def chats_for(self, viewer_id: int) -> list[dict[str, Any]]:
    chats: list[dict[str, Any]] = []
    seen: set[int] = set()
    for chat in self.chats.values():
      member = chat.members.get(viewer_id)
      if member is None or member.status not in ACTIVE_STATUSES:
        continue
      seen.add(chat.id)
      chats.append(chat_card(chat))
    for (user_id, bot_id) in self.bot_chats:
      if user_id != viewer_id or bot_id in seen:
        continue
      seen.add(bot_id)
      chats.append(_chat_of(self.users[bot_id]))
    for peers in self.private_user_chats:
      if viewer_id not in peers:
        continue
      peer_id = next(pid for pid in peers if pid != viewer_id)
      if peer_id in seen:
        continue
      seen.add(peer_id)
      chats.append(_chat_of(self.users[peer_id]))
    return chats

  def push_update(self, token: str, payload: dict[str, Any]) -> int:
    bot = self.bots[token]
    self._open_dialog_from_inbound(bot, payload)
    update_id = bot._next_update_id
    bot._next_update_id += 1
    update = {"update_id": update_id, **payload}
    if bot.webhook_url:
      task = asyncio.create_task(self._deliver_webhook(bot, bot.webhook_url, update))
      self._webhook_tasks.add(task)
      task.add_done_callback(self._webhook_tasks.discard)
      return update_id
    bot.updates.append(update)
    bot._arrived.set()
    return update_id

  def _open_dialog_from_inbound(self, bot: BotRuntime, payload: dict[str, Any]) -> None:
    """A person wrote the bot in private — the dialog is open, the bot can reply."""
    message = payload.get("message")
    if not isinstance(message, dict):
      return
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = chat.get("id")
    from_id = from_user.get("id")
    if chat.get("type") != "private" or not isinstance(chat_id, int) or chat_id <= 0:
      return
    if not isinstance(from_id, int) or from_user.get("is_bot"):
      return
    if from_id not in self.users:
      self.create_user(
        id=from_id,
        first_name=str(from_user.get("first_name") or "Test"),
        username=from_user.get("username"),
      )
    self.ensure_private_chat(from_id, bot.user["id"])

  async def _deliver_webhook(
    self, bot: BotRuntime, url: str, update: dict[str, Any]
  ) -> None:
    if not update_is_visible(bot, update):
      return
    try:
      async with httpx.AsyncClient() as client:
        response = await client.post(url, json=update)
    except Exception as exc:
      bot.last_error_date = int(time.time())
      bot.last_error_message = str(exc)
      return
    if not 200 <= response.status_code < 300:
      bot.last_error_date = int(time.time())
      bot.last_error_message = (
        f"Wrong response from the webhook: {response.status_code} {response.reason_phrase}"
      )

  async def drain_webhooks(self) -> None:
    """Wait for delivery: otherwise stopping the server tears the POST mid-sentence."""
    while self._webhook_tasks:
      await asyncio.gather(*tuple(self._webhook_tasks), return_exceptions=True)

  def ack(self, token: str, offset: int) -> None:
    bot = self.bots[token]
    if offset - 1 > bot.acked:
      bot.acked = offset - 1
      bot._acked.set()

  async def wait_acked(self, token: str, update_id: int, timeout: float) -> bool:
    """Wait for the update to be acked. False — did not make it within timeout."""
    bot = self.bots[token]
    deadline = time.monotonic() + timeout
    while bot.acked < update_id:
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        return False
      # There is no await between the check and clear(), so ack() cannot
      # slip through unnoticed: it runs synchronously.
      bot._acked.clear()
      try:
        await asyncio.wait_for(bot._acked.wait(), timeout=remaining)
      except TimeoutError:
        return False
    return True

  async def take_updates(
    self, token: str, offset: int | None, timeout: float
  ) -> list[dict[str, Any]]:
    bot = self.bots[token]
    if offset is not None:
      self.ack(token, offset)
    deadline = time.monotonic() + timeout
    while True:
      pending = [
        u
        for u in bot.updates
        if (offset is None or u["update_id"] >= offset) and update_is_visible(bot, u)
      ]
      if pending:
        return pending
      remaining = deadline - time.monotonic()
      if remaining <= 0:
        return []
      bot._arrived.clear()
      try:
        await asyncio.wait_for(bot._arrived.wait(), timeout=remaining)
      except TimeoutError:
        return []

  def set_reply_keyboard(
    self, user_id: int, chat_id: int, keyboard: list[list[str]] | None
  ) -> None:
    self._reply_keyboards[(user_id, chat_id)] = keyboard

  def reply_keyboard(self, user_id: int, chat_id: int) -> list[list[str]] | None:
    return self._reply_keyboards.get((user_id, chat_id))

  def inject_error(self, token: str, chat_id: int, status: int, body: dict[str, Any]) -> None:
    self.bots[token].errors[chat_id] = (status, body)

  def pop_error(self, token: str, chat_id: int) -> tuple[int, dict[str, Any]] | None:
    bot = self.bots.get(token)
    if bot is None:
      return None
    return bot.errors.pop(chat_id, None)

  def append_bot_message(
    self, token: str, chat_id: int, message: dict[str, Any]
  ) -> dict[str, Any]:
    bot = self.bots[token]
    chat = self.chats.get(chat_id)
    if chat is not None:
      msg = dict(message)
      msg["message_id"] = max((m.get("message_id", 0) for m in chat.messages), default=0) + 1
      msg["date"] = int(time.time())
      chat.messages.append(msg)
      chat.last_bot_id = bot.user["id"]
      self.emit_chat_message(chat, msg)
      return msg
    key = (chat_id, bot.user["id"])
    thread = self.bot_chats.get(key)
    if thread is None:
      raise KeyError(chat_id)
    msg = dict(message)
    msg["message_id"] = max((m.get("message_id", 0) for m in thread), default=0) + 1
    msg["date"] = int(time.time())
    thread.append(msg)
    self.emit(
      {
        "type": "message",
        "peer_id": bot.user["id"],
        "viewer_id": chat_id,
        "message": {
          **dict(msg),
          "chat": _chat_of(bot.user),
        },
      }
    )
    return msg

  def edit_bot_message(
    self, token: str, chat_id: int, message_id: int, **fields: Any
  ) -> dict[str, Any] | None:
    chat = self.chats.get(chat_id)
    if chat is not None:
      for msg in chat.messages:
        if msg.get("message_id") == message_id:
          msg.update(fields)
          self.emit_chat_message(chat, msg, "message_edited")
          return msg
      return None
    bot = self.bots[token]
    thread = self.bot_chats.get((chat_id, bot.user["id"]))
    if thread is None:
      return None
    for msg in thread:
      if msg.get("message_id") == message_id:
        msg.update(fields)
        self.emit(
          {
            "type": "message_edited",
            "peer_id": bot.user["id"],
            "viewer_id": chat_id,
            "message": {
              **dict(msg),
              "chat": _chat_of(bot.user),
            },
          }
        )
        return msg
    return None

  def dump(self) -> dict[str, Any]:
    bots: list[dict[str, Any]] = []
    for token, runtime in self.bots.items():
      item: dict[str, Any] = {
        "token": token,
        "user": runtime.user,
        "privacy_mode": runtime.privacy_mode,
        "updates": runtime.updates,
        "acked": runtime.acked,
        "errors": [
          {"chat_id": chat_id, "status": status, "body": body}
          for chat_id, (status, body) in runtime.errors.items()
        ],
        "next_update_id": runtime._next_update_id,
        "webhook_url": runtime.webhook_url,
        "last_error_date": runtime.last_error_date,
        "last_error_message": runtime.last_error_message,
        "allowed_updates_cutoff": runtime.allowed_updates_cutoff,
      }
      if runtime.allowed_updates is not None:
        item["allowed_updates"] = list(runtime.allowed_updates)
      bots.append(item)
    return {
      "users": list(self.users.values()),
      "bots": bots,
      "private_user_chats": [
        {"peers": sorted(peers), "messages": messages}
        for peers, messages in self.private_user_chats.items()
      ],
      "bot_chats": [
        {"chat_id": chat_id, "bot_id": bot_id, "messages": messages}
        for (chat_id, bot_id), messages in self.bot_chats.items()
      ],
      "chats": [chat_dump(c) for c in self.chats.values()],
      "journal": {
        "calls": [journal_item(rec) for rec in self.journal.calls()]
      },
      "files": {
        path: base64.b64encode(content).decode("ascii")
        for path, content in self.files.items()
      },
      "reply_keyboards": [
        {"user_id": user_id, "chat_id": chat_id, "keyboard": keyboard}
        for (user_id, chat_id), keyboard in self._reply_keyboards.items()
      ],
      "sessions": [
        {"token": token, "user_id": user_id}
        for token, user_id in self._sessions.items()
      ],
      "next_user_id": self._next_user_id,
      "next_callback_id": self._next_callback_id,
      "callbacks": [
        {
          "query_id": query_id,
          "user_id": rec["user_id"],
          "peer_id": rec["peer_id"],
          "message_id": rec["message_id"],
          "date": rec["date"],
        }
        for query_id, rec in self._callbacks.items()
      ],
    }

  def load(self, data: dict[str, Any]) -> None:
    self.users = {user["id"]: dict(user) for user in data.get("users", [])}
    self.bots = {}
    for bot in data.get("bots", []):
      self.bots[bot["token"]] = BotRuntime(
        user=dict(bot["user"]),
        privacy_mode=bool(bot.get("privacy_mode", True)),
        updates=list(bot.get("updates", [])),
        acked=int(bot.get("acked", 0)),
        errors={
          int(item["chat_id"]): (int(item["status"]), dict(item["body"]))
          for item in bot.get("errors", [])
        },
        _next_update_id=int(bot.get("next_update_id", 1)),
        webhook_url=str(bot.get("webhook_url") or ""),
        last_error_date=(
          None if bot.get("last_error_date") is None else int(bot["last_error_date"])
        ),
        last_error_message=bot.get("last_error_message"),
        allowed_updates=(
          None
          if bot.get("allowed_updates") is None
          else [str(x) for x in bot["allowed_updates"]]
        ),
        allowed_updates_cutoff=int(bot.get("allowed_updates_cutoff", 0)),
      )
    self.private_user_chats = {
      frozenset(item["peers"]): list(item["messages"])
      for item in data.get("private_user_chats", [])
    }
    self.bot_chats = {
      (int(item["chat_id"]), int(item["bot_id"])): list(item["messages"])
      for item in data.get("bot_chats", [])
    }
    self.chats = {
      rec.id: rec
      for rec in (chat_load(item) for item in data.get("chats", []))
    }
    self.journal = Journal()
    for rec in data.get("journal", {}).get("calls", []):
      self.journal.record(
        rec["method"],
        rec["token"],
        rec["kind"],
        rec.get("params"),
        status=rec.get("status"),
        response=rec.get("response"),
      )
    self.files = {
      path: base64.b64decode(encoded)
      for path, encoded in data.get("files", {}).items()
    }
    self._reply_keyboards = {
      (int(item["user_id"]), int(item["chat_id"])): item["keyboard"]
      for item in data.get("reply_keyboards", [])
    }
    self._sessions = {
      str(item["token"]): int(item["user_id"])
      for item in data.get("sessions", [])
    }
    self._next_user_id = int(data.get("next_user_id", 1))
    self._next_callback_id = int(data.get("next_callback_id", 1))
    self._callbacks = {
      str(item["query_id"]): {
        "user_id": int(item["user_id"]),
        "peer_id": int(item["peer_id"]),
        "message_id": int(item["message_id"]),
        "date": int(item.get("date", time.time())),
      }
      for item in data.get("callbacks", [])
    }
