from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from telemulator.bot_api import ensure_bot
from telemulator.chats import ACTIVE_STATUSES, ChatRecord, chat_card, member_json
from telemulator.journal import journal_item
from telemulator.limits import limiter_for_profile
from telemulator.network import Network
from telemulator.user_api import actor_can_add

router = APIRouter()


def _net(request: Request) -> Network:
  return request.app.state.network


@router.post("/admin/reset")
async def admin_reset(request: Request) -> dict[str, str]:
  request.app.state.network = _net(request).reset()
  return {"status": "ok"}


@router.post("/admin/users")
async def admin_create_user(request: Request, body: dict[str, Any]) -> dict[str, Any]:
  user = _net(request).create_user(
    id=body.get("id"),
    first_name=body["first_name"],
    username=body.get("username"),
  )
  return {"user": user}


@router.post("/admin/bots")
async def admin_create_bot(request: Request, body: dict[str, Any]) -> dict[str, Any]:
  net = _net(request)
  user = net.create_bot(
    token=body["token"],
    first_name=body.get("first_name", "Demo"),
    username=body.get("username"),
  )
  if "privacy" in body:
    net.bots[body["token"]].privacy_mode = bool(body["privacy"])
  return {"user": user, "token": body["token"]}


@router.post("/admin/bots/privacy")
async def admin_bot_privacy(request: Request, body: dict[str, Any]) -> dict[str, str]:
  net = _net(request)
  runtime = ensure_bot(net, body["token"])
  if runtime is None:
    raise HTTPException(status_code=400, detail="bad bot token")
  runtime.privacy_mode = bool(body["privacy"])
  return {"status": "ok"}


@router.post("/admin/dialogs")
async def admin_open_dialog(request: Request, body: dict[str, Any]) -> dict[str, Any]:
  net = _net(request)
  user_id = int(body["user_id"])
  if user_id not in net.users:
    raise HTTPException(status_code=400, detail="unknown user")
  runtime = ensure_bot(net, body["bot_token"])
  if runtime is None:
    raise HTTPException(status_code=400, detail="bad bot token")
  chat = net.ensure_private_chat(user_id, runtime.user["id"])
  return {"chat": chat}


@router.post("/admin/outbound-chats")
async def admin_outbound_chat(request: Request, body: dict[str, Any]) -> dict[str, str]:
  net = _net(request)
  runtime = ensure_bot(net, body["bot_token"])
  if runtime is None:
    raise HTTPException(status_code=400, detail="bad bot token")
  net.ensure_outbound_chat(int(body["chat_id"]), runtime.user["id"])
  return {"status": "ok"}


@router.post("/admin/snapshot")
async def admin_snapshot(request: Request) -> dict[str, Any]:
  return _net(request).dump()


@router.post("/admin/snapshot/restore")
async def admin_restore(request: Request, body: dict[str, Any]) -> dict[str, str]:
  _net(request).load(body)
  return {"status": "ok"}


@router.post("/admin/limits")
async def admin_set_limits(request: Request, body: dict[str, Any]) -> dict[str, str]:
  profile = body.get("profile")
  try:
    request.app.state.limiter = limiter_for_profile(profile)
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"status": "ok"}


@router.post("/admin/errors")
async def admin_inject_error(request: Request, body: dict[str, Any]) -> dict[str, str]:
  _net(request).inject_error(
    body["token"], int(body["chat_id"]), int(body["status"]), body["body"]
  )
  return {"status": "ok"}


@router.get("/admin/journal")
async def admin_journal(request: Request) -> dict[str, Any]:
  journal = _net(request).journal
  return {
    "calls": [journal_item(rec) for rec in journal.calls()],
    "unimplemented": [journal_item(rec) for rec in journal.unimplemented()],
  }


def _chat_or_400(net: Network, chat_id: int):
  chat = net.chats.get(chat_id)
  if chat is None:
    raise HTTPException(status_code=400, detail="unknown chat")
  return chat


def _actor_id_for(chat: ChatRecord, actor_id: int | None) -> int:
  if actor_id is not None:
    return actor_id
  return next(m.user_id for m in chat.members.values() if m.status == "creator")


@router.post("/admin/chats")
async def admin_create_chat(request: Request, body: dict[str, Any]) -> dict[str, Any]:
  net = _net(request)
  try:
    record = net.create_chat(
      type=str(body["type"]),
      title=str(body.get("title") or ""),
      creator_id=int(body["creator_id"]),
      member_ids=body.get("member_ids"),
      members=body.get("members"),
    )
  except (KeyError, ValueError, PermissionError) as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"chat": chat_card(record)}


@router.post("/admin/chats/{chat_id}/members")
async def admin_post_member(
  chat_id: int, request: Request, body: dict[str, Any], actor_id: int | None = None
) -> dict[str, Any]:
  net = _net(request)
  chat = _chat_or_400(net, chat_id)
  viewer_id = _actor_id_for(chat, actor_id)
  actor = chat.members.get(viewer_id)
  if actor is None or actor.status not in ACTIVE_STATUSES:
    raise HTTPException(status_code=403, detail="not a member")
  user_id = int(body["user_id"])
  if user_id not in net.users:
    raise HTTPException(status_code=400, detail="unknown user")
  target_is_bot = bool(net.users[user_id].get("is_bot"))
  if not actor_can_add(chat, actor, target_is_bot):
    raise HTTPException(status_code=403, detail="cannot add")
  flags = {k: bool(v) for k, v in body.items() if k.startswith("can_")}
  try:
    member = net.add_member(
      chat_id, user_id, actor_id=viewer_id, status=body.get("status"), flags=flags or None
    )
  except PermissionError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from None
  except KeyError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"member": member_json(net, member, chat, include_can_be_edited=False)}


@router.patch("/admin/chats/{chat_id}/members/{user_id}")
async def admin_patch_member(
  chat_id: int,
  user_id: int,
  request: Request,
  body: dict[str, Any],
  actor_id: int | None = None,
) -> dict[str, Any]:
  net = _net(request)
  chat = _chat_or_400(net, chat_id)
  viewer_id = _actor_id_for(chat, actor_id)
  flags = {k: bool(v) for k, v in body.items() if k.startswith("can_")}
  try:
    member = net.patch_member(
      chat_id,
      user_id,
      actor_id=viewer_id,
      status=str(body["status"]),
      flags=flags,
      require_creator=False,
    )
  except PermissionError:
    raise HTTPException(status_code=403, detail="cannot patch") from None
  except ValueError:
    raise HTTPException(status_code=400, detail="owner") from None
  except KeyError:
    raise HTTPException(status_code=400, detail="unknown user") from None
  return {"member": member_json(net, member, chat, include_can_be_edited=False)}


@router.delete("/admin/chats/{chat_id}/members/{user_id}")
async def admin_delete_member(
  chat_id: int, user_id: int, request: Request, actor_id: int | None = None
) -> dict[str, Any]:
  net = _net(request)
  chat = _chat_or_400(net, chat_id)
  viewer_id = _actor_id_for(chat, actor_id)
  actor = chat.members.get(viewer_id)
  if actor is None or actor.status not in ACTIVE_STATUSES:
    raise HTTPException(status_code=403, detail="not a member")
  if user_id not in chat.members:
    raise HTTPException(status_code=400, detail="unknown member")
  creator_id = next(m.user_id for m in chat.members.values() if m.status == "creator")
  if user_id == creator_id:
    raise HTTPException(status_code=400, detail="owner")
  if user_id != viewer_id and actor.status not in {"creator", "administrator"}:
    raise HTTPException(status_code=403, detail="cannot remove")
  member = net.remove_member(chat_id, user_id, actor_id=viewer_id)
  return {"member": member_json(net, member, chat, include_can_be_edited=False)}


@router.post("/admin/{token}/updates")
async def admin_push_message(token: str, body: dict[str, Any], request: Request) -> dict[str, int]:
  net = _net(request)
  runtime = ensure_bot(net, token)
  if runtime is None:
    raise HTTPException(status_code=400, detail="bad bot token")
  chat_id = int(body["chat_id"])
  if chat_id not in net.users:
    net.create_user(id=chat_id, first_name="Compose")
  net.ensure_private_chat(chat_id, runtime.user["id"])
  update_id = net.push_update(
    token,
    {
      "message": {
        "message_id": 1,
        "date": int(time.time()),
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": chat_id, "is_bot": False, "first_name": "Compose"},
        "text": body["text"],
      }
    },
  )
  return {"update_id": update_id}


@router.post("/admin/{token}/callback")
async def admin_push_callback(token: str, body: dict[str, Any], request: Request) -> dict[str, int]:
  net = _net(request)
  runtime = ensure_bot(net, token)
  bot_id = runtime.user["id"] if runtime is not None else int(token.split(":")[0])
  chat_id = int(body["chat_id"])
  last = net.bot_chats.get((chat_id, bot_id), [])[-1]
  query_id = net.new_callback_id()
  net.register_callback(query_id, chat_id, bot_id, last["message_id"])
  update_id = net.push_update(
    token,
    {
      "callback_query": {
        "id": query_id,
        "from": {"id": chat_id, "is_bot": False, "first_name": "Compose"},
        "chat_instance": "compose",
        "data": body["callback_data"],
        "message": {
          "message_id": last["message_id"],
          "date": int(time.time()),
          "chat": {"id": chat_id, "type": "private"},
          "text": last.get("text"),
        },
      }
    },
  )
  return {"update_id": update_id}


@router.get("/admin/{token}/messages")
async def admin_messages(token: str, chat_id: int, request: Request) -> list[dict[str, Any]]:
  net = _net(request)
  runtime = ensure_bot(net, token)
  bot_id = runtime.user["id"] if runtime is not None else int(token.split(":")[0])
  out: list[dict[str, Any]] = []
  for msg in net.bot_chats.get((chat_id, bot_id), []):
    markup = msg.get("reply_markup") or {}
    inline = markup.get("inline_keyboard") or []
    out.append(
      {
        "message_id": msg["message_id"],
        "text": msg.get("text"),
        "inline_keyboard": [
          [
            {
              "text": btn.get("text"),
              "callback_data": btn.get("callback_data"),
              "url": btn.get("url"),
            }
            for btn in row
          ]
          for row in inline
        ],
      }
    )
  return out
