from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from telemulator.chats import ACTIVE_STATUSES, chat_card, member_json
from telemulator.network import Network
from telemulator.user_api import (
  _press,
  actor_can_add,
  list_members_json,
  message_for_viewer,
  send_text,
)

COOKIE = "telemulator_session"
router = APIRouter()


def _net(request: Request) -> Network:
  return request.app.state.network


def _token_from(request: Request) -> str | None:
  auth = request.headers.get("authorization") or ""
  if auth.lower().startswith("bearer "):
    return auth.split(" ", 1)[1].strip()
  return request.cookies.get(COOKIE)


def _viewer_id(request: Request) -> int:
  token = _token_from(request)
  if not token:
    raise HTTPException(status_code=401, detail="no session")
  net = _net(request)
  user_id = net.user_id_for_session(token)
  if user_id is None or user_id not in net.users:
    raise HTTPException(status_code=401, detail="no session")
  return user_id


@router.post("/user/sessions")
async def create_session(request: Request, body: dict[str, Any]) -> Response:
  net = _net(request)
  user_id = int(body["user_id"])
  if user_id not in net.users or net.users[user_id].get("is_bot"):
    raise HTTPException(status_code=400, detail="unknown user")
  token = net.create_session(user_id)
  response = JSONResponse({"token": token, "user": net.users[user_id]})
  response.set_cookie(COOKIE, token, httponly=False, samesite="lax", path="/")
  return response


@router.get("/user/me")
async def me(request: Request) -> dict[str, Any]:
  return _net(request).users[_viewer_id(request)]


@router.get("/user/chats")
async def list_chats(request: Request) -> dict[str, Any]:
  return {"chats": _net(request).chats_for(_viewer_id(request))}


@router.post("/user/chats")
async def create_chat(request: Request, body: dict[str, Any]) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  chat_type = str(body.get("type") or "")
  if chat_type not in {"group", "supergroup", "channel"}:
    raise HTTPException(status_code=400, detail="invalid type")
  try:
    record = net.create_chat(
      type=chat_type,
      title=str(body.get("title") or ""),
      creator_id=viewer_id,
      member_ids=body.get("member_ids"),
      members=body.get("members"),
    )
  except (KeyError, ValueError, PermissionError) as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"chat": chat_card(record)}


def _chat_or_400(net: Network, chat_id: int):
  chat = net.chats.get(chat_id)
  if chat is None:
    raise HTTPException(status_code=400, detail="unknown chat")
  return chat


@router.get("/user/chats/{chat_id}/members")
async def get_members(chat_id: int, request: Request) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  chat = _chat_or_400(net, chat_id)
  me = chat.members.get(viewer_id)
  if me is None or me.status not in ACTIVE_STATUSES:
    raise HTTPException(status_code=403, detail="not a member")
  return {"members": list_members_json(net, chat)}


@router.post("/user/chats/{chat_id}/members")
async def post_member(chat_id: int, request: Request, body: dict[str, Any]) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  chat = _chat_or_400(net, chat_id)
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


@router.patch("/user/chats/{chat_id}/members/{user_id}")
async def patch_member_http(
  chat_id: int, user_id: int, request: Request, body: dict[str, Any]
) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  chat = _chat_or_400(net, chat_id)
  flags = {k: bool(v) for k, v in body.items() if k.startswith("can_")}
  try:
    member = net.patch_member(
      chat_id, user_id, actor_id=viewer_id, status=str(body["status"]), flags=flags
    )
  except PermissionError:
    raise HTTPException(status_code=403, detail="cannot patch") from None
  except ValueError:
    raise HTTPException(status_code=400, detail="owner") from None
  except KeyError:
    raise HTTPException(status_code=400, detail="unknown user") from None
  return {"member": member_json(net, member, chat, include_can_be_edited=False)}


@router.delete("/user/chats/{chat_id}/members/{user_id}")
async def delete_member(chat_id: int, user_id: int, request: Request) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  chat = _chat_or_400(net, chat_id)
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


@router.get("/user/chats/{peer_id}/messages")
async def list_messages(peer_id: int, request: Request) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  messages = [
    message_for_viewer(net, msg, peer_id)
    for msg in net.thread_for(viewer_id, peer_id)
  ]
  keyboard = None
  if peer_id in net.chats:
    keyboard = net.reply_keyboard(viewer_id, peer_id)
  elif net._is_bot_id(peer_id):
    keyboard = net.reply_keyboard(viewer_id, peer_id)
  return {
    "messages": messages,
    "reply_keyboard": keyboard,
  }


@router.post("/user/chats/{peer_id}/messages")
async def post_message(
  peer_id: int, request: Request, body: dict[str, Any]
) -> dict[str, Any]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  text = str(body.get("text") or "")
  reply_to_message_id = body.get("reply_to_message_id")
  try:
    send_text(
      net, viewer_id, peer_id, text, reply_to_message_id=reply_to_message_id
    )
  except KeyError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  except PermissionError as exc:
    raise HTTPException(status_code=403, detail=str(exc)) from exc
  stored = net.thread_for(viewer_id, peer_id)[-1]
  return {"message": message_for_viewer(net, stored, peer_id)}


@router.post("/user/chats/{peer_id}/messages/{message_id}/press")
async def press(
  peer_id: int, message_id: int, request: Request, body: dict[str, Any]
) -> dict[str, str]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  data = str(body.get("data") or "")
  try:
    _, query_id = _press(net, viewer_id, peer_id, message_id, data)
  except KeyError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"query_id": query_id}


@router.get("/user/files/{path:path}")
async def user_file(path: str, request: Request) -> Response:
  _viewer_id(request)
  content = _net(request).files.get(path)
  if content is None:
    raise HTTPException(status_code=404, detail="file not found")
  return Response(content=content, media_type="application/octet-stream")


@router.get("/user/events")
async def events(request: Request) -> StreamingResponse:
  net = _net(request)
  viewer_id = _viewer_id(request)
  queue = net.subscribe()

  async def gen():
    try:
      while True:
        if await request.is_disconnected():
          break
        try:
          event = await asyncio.wait_for(queue.get(), timeout=15.0)
        except TimeoutError:
          yield ": keep-alive\n\n"
          continue
        if event.get("type") == "journal":
          pass
        elif event.get("type") == "callback_answered":
          pass
        elif event.get("viewer_id") not in (None, viewer_id):
          continue
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
      # После reset очередь уже на новой сети; captured `net` пустой.
      current = _net(request)
      current.unsubscribe(queue)
      if current is not net:
        net.unsubscribe(queue)

  return StreamingResponse(gen(), media_type="text/event-stream")
