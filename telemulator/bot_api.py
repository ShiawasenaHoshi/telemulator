from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from telemulator.catalog import classify_method
from telemulator.chats import ACTIVE_STATUSES, ADMIN_FLAGS, ChatRecord, Member, chat_card, member_json
from telemulator.errors import (
  CANT_INITIATE,
  CANT_PROMOTE_OWNER,
  CANT_REMOVE_OWNER,
  CHAT_NOT_FOUND,
  CONFLICT_GETUPDATES,
  KICKED_CHANNEL,
  KICKED_GROUP,
  KICKED_SUPER,
  MESSAGE_TO_EDIT,
  METHOD_SUPER_CHANNEL,
  NOT_ENOUGH_PROMOTE,
  NOT_ENOUGH_RESTRICT,
  NOT_ENOUGH_RIGHTS_SEND,
  NOT_FOUND,
  NOT_MEMBER_CHANNEL,
  NOT_MEMBER_GROUP,
  NOT_MEMBER_SUPER,
  QUERY_TOO_OLD,
  UNAUTHORIZED,
  USER_NOT_FOUND,
  bot_error,
)
from telemulator.journal import journal_item
from telemulator.limits import RateLimiter
from telemulator.network import (
  BotRuntime,
  Network,
  _chat_of,
  apply_allowed_updates,
  update_is_visible,
)
from telemulator.privacy import deliver_channel_post_to_bots, deliver_group_message_to_bots
from telemulator.view import parse_markup

IMPLEMENTED = frozenset(
  {
    "getMe",
    "getUpdates",
    "sendMessage",
    "editMessageText",
    "editMessageReplyMarkup",
    "answerCallbackQuery",
    "sendPhoto",
    "sendDocument",
    "getFile",
    "getUserProfilePhotos",
    "setWebhook",
    "deleteWebhook",
    "getWebhookInfo",
    "getChat",
    "getChatMember",
    "getChatAdministrators",
    "getChatMemberCount",
    "leaveChat",
    "banChatMember",
    "unbanChatMember",
    "promoteChatMember",
  }
)

# Reads: the response does not change the network, so there is nothing to persist
# after them. getUpdates advances acked, but that is the bot's cursor, not network
# state: after a sandbox restart it will recover on the first offset.
READ_ONLY = frozenset(
  {
    "getMe",
    "getUpdates",
    "getFile",
    "getWebhookInfo",
    "getUserProfilePhotos",
    "getChat",
    "getChatMember",
    "getChatAdministrators",
    "getChatMemberCount",
  }
)

router = APIRouter()


def _ok(result: Any) -> JSONResponse:
  return JSONResponse({"ok": True, "result": result})


def _err(status: int, body: dict[str, Any]) -> JSONResponse:
  return JSONResponse(body, status_code=status)


def _flag(params: dict[str, Any], name: str, default: bool = False) -> bool:
  if name not in params:
    return default
  return str(params[name]).lower() in {"true", "1", "yes"}


def _response_body(response: Response) -> dict[str, Any] | None:
  raw = getattr(response, "body", None)
  if not raw:
    return None
  try:
    body = json.loads(raw)
  except (TypeError, json.JSONDecodeError):
    return None
  return body if isinstance(body, dict) else None


def _journal_response(
  method: str, body: dict[str, Any] | None
) -> dict[str, Any] | None:
  if body is None:
    return None
  if method == "getUpdates" and isinstance(body.get("result"), list):
    return {"ok": body.get("ok"), "result_count": len(body["result"])}
  return body


def _journal(
  network: Network,
  method: str,
  token: str,
  kind: str,
  params: dict[str, Any],
  *,
  status: int,
  response: dict[str, Any] | None,
) -> None:
  rec = network.journal.record(method, token, kind, params, status=status, response=response)
  network.emit({"type": "journal", "record": journal_item(rec)})


def _bot_id(token: str) -> int | None:
  if ":" not in token:
    return None
  prefix = token.split(":", 1)[0]
  try:
    return int(prefix)
  except ValueError:
    return None


def ensure_bot(network: Network, token: str) -> BotRuntime | None:
  bot_id = _bot_id(token)
  if bot_id is None:
    return None
  if network.bot_by_token(token) is None:
    network.create_bot(token=token, username=f"telemulator{bot_id}")
  return network.bots[token]


def _from_bot(runtime: BotRuntime) -> dict[str, Any]:
  user = runtime.user
  bot: dict[str, Any] = {
    "id": user["id"],
    "is_bot": True,
    "first_name": user["first_name"],
  }
  if "username" in user:
    bot["username"] = user["username"]
  return bot


def _apply_reply_keyboard(
  network: Network, user_id: int, bot_id: int, markup_raw: str | None
) -> None:
  _inline, reply, removed = parse_markup(markup_raw)
  if removed:
    network.set_reply_keyboard(user_id, bot_id, None)
  elif reply is not None:
    network.set_reply_keyboard(user_id, bot_id, reply)


def _apply_group_reply_keyboard(
  network: Network, chat: ChatRecord, markup_raw: str | None
) -> None:
  _inline, reply, removed = parse_markup(markup_raw)
  if not removed and reply is None:
    return
  keyboard = None if removed else reply
  for member in chat.members.values():
    if member.status not in ACTIVE_STATUSES:
      continue
    network.set_reply_keyboard(member.user_id, chat.id, keyboard)


def _membership_error(chat: ChatRecord, member: Member | None) -> Response | None:
  table_left = {
    "group": NOT_MEMBER_GROUP,
    "supergroup": NOT_MEMBER_SUPER,
    "channel": NOT_MEMBER_CHANNEL,
  }
  table_kick = {
    "group": KICKED_GROUP,
    "supergroup": KICKED_SUPER,
    "channel": KICKED_CHANNEL,
  }
  if member is None or member.status == "left":
    return _err(*bot_error(403, table_left[chat.type]))
  if member.status == "kicked":
    return _err(*bot_error(403, table_kick[chat.type]))
  return None


def _caller_is_admin(member: Member | None) -> bool:
  return member is not None and member.status in {"creator", "administrator"}


def _missing_chat_error(network: Network, chat_id: int, bot_id: int) -> Response:
  if (chat_id, bot_id) not in network.bot_chats:
    user = network.users.get(chat_id)
    if user is not None and not user.get("is_bot"):
      return _err(*bot_error(403, CANT_INITIATE))
  return _err(*bot_error(400, CHAT_NOT_FOUND))


def _group_chat_for_bot(
  network: Network, token: str, params: dict[str, Any]
) -> ChatRecord | Response:
  chat_id = int(params["chat_id"])
  bot_id = network.bots[token].user["id"]
  record = network.chats.get(chat_id)
  if record is None:
    return _missing_chat_error(network, chat_id, bot_id)
  denied = _membership_error(record, record.members.get(bot_id))
  if denied is not None:
    return denied
  return record


def _get_chat(network: Network, token: str, params: dict[str, Any]) -> Response:
  chat_id = int(params["chat_id"])
  bot_id = network.bots[token].user["id"]
  record = network.chats.get(chat_id)
  if record is not None:
    denied = _membership_error(record, record.members.get(bot_id))
    if denied is not None:
      return denied
    return _ok(chat_card(record))
  if (chat_id, bot_id) in network.bot_chats:
    user = network.users.get(chat_id)
    if user is not None:
      return _ok(_chat_of(user))
    return _ok({"id": chat_id, "type": "private"})
  return _missing_chat_error(network, chat_id, bot_id)


def _get_chat_member(network: Network, token: str, params: dict[str, Any]) -> Response:
  # Not 403-first: left/kicked self is 200; no members record is 400 even for self.
  chat_id = int(params["chat_id"])
  user_id = int(params["user_id"])
  bot_id = network.bots[token].user["id"]
  record = network.chats.get(chat_id)
  if record is None:
    return _missing_chat_error(network, chat_id, bot_id)
  caller = record.members.get(bot_id)
  target = record.members.get(user_id)
  if target is None:
    return _err(*bot_error(400, USER_NOT_FOUND))
  if target.status in {"left", "kicked"}:
    if user_id == bot_id or _caller_is_admin(caller):
      return _ok(member_json(network, target, record, caller_bot_id=bot_id))
    return _err(*bot_error(400, USER_NOT_FOUND))
  return _ok(member_json(network, target, record, caller_bot_id=bot_id))


def _get_chat_administrators(
  network: Network, token: str, params: dict[str, Any]
) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  bot_id = network.bots[token].user["id"]
  return_bots = str(params.get("return_bots") or "").lower() in {"true", "1"}
  result: list[dict[str, Any]] = []
  for member in record.members.values():
    if member.status not in {"creator", "administrator"}:
      continue
    user = network.users[member.user_id]
    if user.get("is_bot") and member.user_id != bot_id and not return_bots:
      continue
    result.append(member_json(network, member, record, caller_bot_id=bot_id))
  return _ok(result)


def _get_chat_member_count(
  network: Network, token: str, params: dict[str, Any]
) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  return _ok(
    sum(1 for member in record.members.values() if member.status in ACTIVE_STATUSES)
  )


def _caller_has_flag(member: Member, flag: str) -> bool:
  return member.status == "creator" or bool(getattr(member, flag))


def _leave_chat(network: Network, token: str, params: dict[str, Any]) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  network.leave_chat(record.id, network.bots[token].user["id"])
  return _ok(True)


def _ban_chat_member(network: Network, token: str, params: dict[str, Any]) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  bot_id = network.bots[token].user["id"]
  caller = record.members[bot_id]
  if not _caller_has_flag(caller, "can_restrict_members"):
    return _err(*bot_error(403, NOT_ENOUGH_RESTRICT))
  user_id = int(params["user_id"])
  target = record.members.get(user_id)
  if target is None:
    return _err(*bot_error(400, USER_NOT_FOUND))
  if target.status == "creator":
    return _err(*bot_error(400, CANT_REMOVE_OWNER))
  network.ban_member(record.id, user_id, actor_id=bot_id)
  return _ok(True)


def _unban_chat_member(
  network: Network, token: str, params: dict[str, Any]
) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  if record.type == "group":
    return _err(*bot_error(400, METHOD_SUPER_CHANNEL))
  bot_id = network.bots[token].user["id"]
  caller = record.members[bot_id]
  if not _caller_has_flag(caller, "can_restrict_members"):
    return _err(*bot_error(403, NOT_ENOUGH_RESTRICT))
  user_id = int(params["user_id"])
  target = record.members.get(user_id)
  if target is None:
    return _err(*bot_error(400, USER_NOT_FOUND))
  network.unban_member(
    record.id,
    user_id,
    actor_id=bot_id,
    only_if_banned=_flag(params, "only_if_banned"),
  )
  return _ok(True)


def _promote_chat_member(
  network: Network, token: str, params: dict[str, Any]
) -> Response:
  record = _group_chat_for_bot(network, token, params)
  if isinstance(record, Response):
    return record
  if record.type == "group":
    return _err(*bot_error(400, METHOD_SUPER_CHANNEL))
  bot_id = network.bots[token].user["id"]
  caller = record.members[bot_id]
  if not _caller_has_flag(caller, "can_promote_members"):
    return _err(*bot_error(400, NOT_ENOUGH_PROMOTE))
  user_id = int(params["user_id"])
  target = record.members.get(user_id)
  if target is None:
    return _err(*bot_error(400, USER_NOT_FOUND))
  if target.status == "creator":
    return _err(*bot_error(400, CANT_PROMOTE_OWNER))
  flags = {
    name: _flag(
      params,
      name,
      default=(record.type == "channel" and name == "can_restrict_members"),
    )
    for name in ADMIN_FLAGS
  }
  network.promote_member(record.id, user_id, actor_id=bot_id, flags=flags)
  return _ok(True)


def _push_to_other_channel_bots(
  network: Network,
  token: str,
  chat: ChatRecord,
  key: str,
  stored: dict[str, Any],
) -> None:
  deliver_channel_post_to_bots(
    network, chat, _public_message(stored), key=key, skip_token=token
  )


def _markup_obj(raw: Any) -> dict[str, Any] | None:
  if not raw:
    return None
  return json.loads(str(raw))


def _public_message(stored: dict[str, Any]) -> dict[str, Any]:
  """Bot API Message: parse_mode and the reply keyboard are an internal snapshot, not the response."""
  public = {key: value for key, value in stored.items() if key != "parse_mode"}
  markup = public.get("reply_markup")
  if isinstance(markup, dict) and "inline_keyboard" not in markup:
    del public["reply_markup"]
  return public


def _outbound_to_chat(
  network: Network,
  token: str,
  params: dict[str, Any],
  fields: dict[str, Any],
  record: ChatRecord,
  limiter: RateLimiter | None,
) -> Response:
  bot_id = network.bots[token].user["id"]
  member = record.members.get(bot_id)
  denied = _membership_error(record, member)
  if denied is not None:
    return denied
  if (
    record.type == "channel"
    and member is not None
    and member.status != "creator"
    and not member.can_post_messages
  ):
    return _err(*bot_error(403, NOT_ENOUGH_RIGHTS_SEND))
  card = chat_card(record)
  message: dict[str, Any] = {"chat": card, **fields}
  if record.type == "channel":
    message["sender_chat"] = card
  else:
    message["from"] = _from_bot(network.bots[token])
  if params.get("parse_mode"):
    message["parse_mode"] = params["parse_mode"]
  markup = _markup_obj(params.get("reply_markup"))
  if markup is not None:
    message["reply_markup"] = markup
  stored = network.append_bot_message(token, record.id, message)
  raw_markup = params.get("reply_markup")
  _apply_group_reply_keyboard(
    network, record, None if raw_markup is None else str(raw_markup)
  )
  if record.type == "channel":
    _push_to_other_channel_bots(network, token, record, "channel_post", stored)
  else:
    deliver_group_message_to_bots(network, record, stored)
  if limiter is not None:
    limiter.record(bot_id, record.id, group_chat=True)
  return _ok(_public_message(stored))


def _outbound_message(
  network: Network,
  token: str,
  params: dict[str, Any],
  fields: dict[str, Any],
  *,
  limiter: RateLimiter | None = None,
) -> Response:
  chat_id = int(params["chat_id"])
  queued = network.pop_error(token, chat_id)
  if queued is not None:
    status_code, body = queued
    return JSONResponse(body, status_code=status_code)
  bot_id = network.bots[token].user["id"]
  group_chat = chat_id in network.chats
  if limiter is not None:
    retry_after = limiter.check(bot_id, chat_id, group_chat=group_chat)
    if retry_after is not None:
      return _err(
        *bot_error(
          429,
          f"Too Many Requests: retry after {retry_after}",
          retry_after=retry_after,
        )
      )
  record = network.chats.get(chat_id)
  if record is not None:
    return _outbound_to_chat(network, token, params, fields, record, limiter)
  if (chat_id, bot_id) not in network.bot_chats and chat_id not in network.chats:
    user = network.users.get(chat_id)
    if user is not None and not user.get("is_bot"):
      return _err(*bot_error(403, CANT_INITIATE))
    return _err(*bot_error(400, CHAT_NOT_FOUND))
  message: dict[str, Any] = {
    "chat": {"id": chat_id, "type": "private"},
    "from": _from_bot(network.bots[token]),
    **fields,
  }
  # Telegram Message does not carry parse_mode; we keep it for markup_guard / BotView.raw.
  if params.get("parse_mode"):
    message["parse_mode"] = params["parse_mode"]
  markup = _markup_obj(params.get("reply_markup"))
  if markup is not None:
    message["reply_markup"] = markup
    _apply_reply_keyboard(network, chat_id, bot_id, str(params.get("reply_markup")))
  try:
    stored = network.append_bot_message(token, chat_id, message)
  except KeyError:
    return _err(*bot_error(400, CHAT_NOT_FOUND))
  if limiter is not None:
    limiter.record(bot_id, chat_id, group_chat=group_chat)
  return _ok(_public_message(stored))


def _get_me(runtime: BotRuntime, params: dict[str, Any]) -> Response:
  user = dict(runtime.user)
  if "username" not in user:
    user["username"] = f"telemulator{user['id']}"
  user["can_join_groups"] = True
  user["can_read_all_group_messages"] = not runtime.privacy_mode
  return _ok(user)


def _send_message(
  network: Network,
  token: str,
  params: dict[str, Any],
  limiter: RateLimiter | None = None,
) -> Response:
  return _outbound_message(
    network, token, params, {"text": str(params.get("text") or "")}, limiter=limiter
  )


def _edit_message_text(network: Network, token: str, params: dict[str, Any]) -> Response:
  chat_id = int(params["chat_id"])
  message_id = int(params["message_id"])
  fields: dict[str, Any] = {"text": str(params.get("text", ""))}
  if params.get("parse_mode"):
    fields["parse_mode"] = params["parse_mode"]
  if params.get("reply_markup"):
    fields["reply_markup"] = json.loads(str(params["reply_markup"]))
  target = network.edit_bot_message(token, chat_id, message_id, **fields)
  if target is None:
    return _err(*bot_error(400, MESSAGE_TO_EDIT))
  record = network.chats.get(chat_id)
  if record is not None and record.type == "channel":
    _push_to_other_channel_bots(network, token, record, "edited_channel_post", target)
  elif record is not None:
    deliver_group_message_to_bots(network, record, target)
  return _ok(
    {
      "message_id": message_id,
      "date": target["date"],
      "chat": target.get("chat") or {"id": chat_id, "type": "private"},
      "text": target["text"],
    }
  )


def _edit_reply_markup(network: Network, token: str, params: dict[str, Any]) -> Response:
  chat_id = int(params["chat_id"])
  message_id = int(params["message_id"])
  markup = _markup_obj(params.get("reply_markup")) or {}
  target = network.edit_bot_message(token, chat_id, message_id, reply_markup=markup)
  if target is None:
    return _err(*bot_error(400, MESSAGE_TO_EDIT))
  result: dict[str, Any] = {
    "message_id": message_id,
    "date": target["date"],
    "chat": target.get("chat") or {"id": chat_id, "type": "private"},
    "text": target.get("text", ""),
  }
  if markup:
    result["reply_markup"] = markup
  return _ok(result)


def _answer_callback_query(network: Network, token: str, params: dict[str, Any]) -> Response:
  query_id = str(params.get("callback_query_id") or "")
  if not network.answer_callback(query_id):
    return _err(*bot_error(400, QUERY_TOO_OLD))
  return _ok(True)


def _send_photo(
  network: Network,
  token: str,
  params: dict[str, Any],
  limiter: RateLimiter | None = None,
) -> Response:
  return _send_media(network, token, params, kind="photo", limiter=limiter)


def _send_document(
  network: Network,
  token: str,
  params: dict[str, Any],
  limiter: RateLimiter | None = None,
) -> Response:
  return _send_media(network, token, params, kind="document", limiter=limiter)


def _send_media(
  network: Network,
  token: str,
  params: dict[str, Any],
  *,
  kind: str,
  limiter: RateLimiter | None = None,
) -> Response:
  fields: dict[str, Any] = {}
  caption = str(params.get("caption") or "")
  if caption:
    fields["caption"] = caption
  # file_id = kind-message_id; put media in fields before emit, else SSE has no attachments.
  chat_id = int(params["chat_id"])
  bot_id = network.bots[token].user["id"]
  record = network.chats.get(chat_id)
  thread = record.messages if record is not None else network.bot_chats.get((chat_id, bot_id), [])
  next_id = max((m.get("message_id", 0) for m in thread), default=0) + 1
  file_id = f"{kind}-{next_id}"
  if kind == "photo":
    fields[kind] = [
      {"file_id": file_id, "file_unique_id": file_id, "width": 160, "height": 160}
    ]
  else:
    document: dict[str, Any] = {"file_id": file_id, "file_unique_id": file_id}
    if params.get("file_name"):
      document["file_name"] = str(params["file_name"])
    fields[kind] = document
  response = _outbound_message(network, token, params, fields, limiter=limiter)
  if response.status_code == 200:
    # Register bytes only for a delivered message: a rejected send must not
    # leave a downloadable file in files or in the snapshot.
    network.files.setdefault(f"{file_id}.bin", b"e2e-file-content")
  return response


def _get_file(network: Network, token: str, params: dict[str, Any]) -> Response:
  file_id = str(params["file_id"])
  path = f"{file_id}.bin"
  network.files.setdefault(path, b"e2e-file-content")
  return _ok(
    {
      "file_id": file_id,
      "file_unique_id": file_id,
      "file_path": path,
      "file_size": len(network.files[path]),
    }
  )


def _get_user_profile_photos(network: Network, token: str, params: dict[str, Any]) -> Response:
  user_id = int(params["user_id"])
  file_id = f"avatar-{user_id}"
  network.files.setdefault(f"{file_id}.bin", b"e2e-avatar-content")
  return _ok(
    {
      "total_count": 1,
      "photos": [[{"file_id": file_id, "file_unique_id": file_id, "width": 160, "height": 160}]],
    }
  )


def _set_webhook(network: Network, token: str, params: dict[str, Any]) -> Response:
  runtime = network.bots[token]
  runtime.webhook_url = str(params.get("url") or "")
  apply_allowed_updates(runtime, params)
  return _ok(True)


def _get_webhook_info(network: Network, token: str, params: dict[str, Any]) -> Response:
  runtime = network.bots[token]
  info: dict[str, Any] = {
    "url": runtime.webhook_url,
    "has_custom_certificate": False,
    "pending_update_count": sum(
      1 for update in runtime.updates if update["update_id"] > runtime.acked
    ),
  }
  if runtime.last_error_date is not None:
    info["last_error_date"] = runtime.last_error_date
  if runtime.last_error_message is not None:
    info["last_error_message"] = runtime.last_error_message
  if runtime.allowed_updates is not None:
    info["allowed_updates"] = runtime.allowed_updates
  return _ok(info)


async def _get_updates(network: Network, token: str, params: dict[str, Any]) -> Response:
  runtime = network.bots[token]
  if runtime.webhook_url:
    return _err(*bot_error(409, CONFLICT_GETUPDATES))
  apply_allowed_updates(runtime, params)
  offset = int(params["offset"]) if "offset" in params else None
  timeout = float(params.get("timeout", 0))

  async def poll() -> list[dict[str, Any]]:
    return await network.take_updates(token, offset, min(timeout, 5.0))

  previous = runtime.pending_getupdates
  task = asyncio.create_task(poll())
  runtime.pending_getupdates = task
  if previous is not None and not previous.done():
    runtime.preempted.add(previous)
    previous.cancel()
  try:
    updates = await task
  except asyncio.CancelledError:
    if task not in runtime.preempted:
      raise  # shutting down the server or the client left — that is not a conflict
    runtime.preempted.discard(task)
    return _err(*bot_error(409, CONFLICT_GETUPDATES))
  finally:
    if runtime.pending_getupdates is task:
      runtime.pending_getupdates = None
  return _ok([u for u in updates if update_is_visible(runtime, u)])


HANDLERS = {
  "getMe": lambda net, token, params: _get_me(net.bots[token], params),
  "sendMessage": _send_message,
  "editMessageText": _edit_message_text,
  "editMessageReplyMarkup": _edit_reply_markup,
  "answerCallbackQuery": _answer_callback_query,
  "sendPhoto": _send_photo,
  "sendDocument": _send_document,
  "getFile": _get_file,
  "getUserProfilePhotos": _get_user_profile_photos,
  "setWebhook": _set_webhook,
  "deleteWebhook": _set_webhook,
  "getWebhookInfo": _get_webhook_info,
  "getChat": _get_chat,
  "getChatMember": _get_chat_member,
  "getChatAdministrators": _get_chat_administrators,
  "getChatMemberCount": _get_chat_member_count,
  "leaveChat": _leave_chat,
  "banChatMember": _ban_chat_member,
  "unbanChatMember": _unban_chat_member,
  "promoteChatMember": _promote_chat_member,
}


@router.post("/bot{token}/{method}")
async def call(token: str, method: str, request: Request) -> Response:
  network: Network = request.app.state.network
  form = await request.form()
  params: dict[str, Any] = {key: form[key] for key in form}
  runtime = ensure_bot(network, token)
  if runtime is None:
    return _err(*bot_error(401, UNAUTHORIZED))
  kind = classify_method(method, IMPLEMENTED)
  journal_params = {k: str(v) for k, v in params.items()}
  if kind != "ok":
    response = _err(*bot_error(404, NOT_FOUND))
    _journal(network, method, token, kind, journal_params,
             status=response.status_code, response=_response_body(response))
    return response
  try:
    if method == "getUpdates":
      response = await _get_updates(network, token, params)
    elif method in {"sendMessage", "sendPhoto", "sendDocument"}:
      response = HANDLERS[method](network, token, params, request.app.state.limiter)
    else:
      response = HANDLERS[method](network, token, params)
  except asyncio.CancelledError:
    # 499 like nginx: the client closed the connection, there was no response.
    # Without this branch a dropped long-poll vanishes from the journal entirely.
    _journal(network, method, token, kind, journal_params,
             status=499, response={"ok": False, "description": "client disconnected"})
    raise
  _journal(network, method, token, kind, journal_params,
           status=response.status_code,
           response=_journal_response(method, _response_body(response)))
  return response


@router.get("/file/bot{token}/{path:path}")
async def download(token: str, path: str, request: Request) -> Response:
  network: Network = request.app.state.network
  if ensure_bot(network, token) is None:
    return _err(*bot_error(401, UNAUTHORIZED))
  content = network.files.get(path)
  if content is None:
    return Response(status_code=404)
  return Response(content=content, media_type="application/octet-stream")
