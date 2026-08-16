from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from telemulator.network import Network


@dataclass(frozen=True)
class Button:
  text: str
  callback_data: str | None = None
  url: str | None = None


@dataclass
class SentMessage:
  message_id: int
  chat_id: int
  text: str
  inline_keyboard: list[list[Button]] = field(default_factory=list)
  reply_keyboard: list[list[str]] | None = None
  reply_keyboard_removed: bool = False
  raw: dict[str, Any] = field(default_factory=dict)

  @property
  def buttons(self) -> list[Button]:
    return [button for row in self.inline_keyboard for button in row]


def parse_markup(raw: str | None) -> tuple[list[list[Button]], list[list[str]] | None, bool]:
  """Split a Telegram reply_markup JSON string into inline / reply / removal."""
  if not raw:
    return [], None, False
  markup = json.loads(raw)
  if markup.get("remove_keyboard"):
    return [], None, True
  inline = [
    [Button(text=b["text"], callback_data=b.get("callback_data"), url=b.get("url")) for b in row]
    for row in markup.get("inline_keyboard", [])
  ]
  reply = None
  if "keyboard" in markup:
    reply = [[b["text"] if isinstance(b, dict) else str(b) for b in row] for row in markup["keyboard"]]
  return inline, reply, False


def _as_markup_json(markup: Any) -> str | None:
  if not markup:
    return None
  if isinstance(markup, str):
    return markup
  return json.dumps(markup)


class BotView:
  """The network as one bot sees it: outgoing messages, queue and ack."""

  def __init__(self, network: Network, token: str) -> None:
    self.network = network
    self.token = token
    if token not in network.bots:
      network.create_bot(token=token)

  @property
  def bot_id(self) -> int:
    runtime = self.network.bots.get(self.token)
    if runtime is not None:
      return int(runtime.user["id"])
    return int(self.token.split(":")[0])

  @property
  def files(self) -> dict[str, bytes]:
    return self.network.files

  def _sent(self, chat_id: int, msg: dict[str, Any]) -> SentMessage:
    inline, reply, removed = parse_markup(_as_markup_json(msg.get("reply_markup")))
    return SentMessage(
      message_id=int(msg["message_id"]),
      chat_id=chat_id,
      text=str(msg.get("text") or msg.get("caption") or ""),
      inline_keyboard=inline,
      reply_keyboard=reply,
      reply_keyboard_removed=removed,
      raw=msg,
    )

  def _bot_thread_messages(self, chat_id: int, thread: list[dict[str, Any]]) -> list[SentMessage]:
    bot_id = self.bot_id
    out: list[SentMessage] = []
    for msg in thread:
      from_user = msg.get("from") or {}
      if from_user.get("id") == bot_id or from_user.get("is_bot"):
        out.append(self._sent(chat_id, msg))
    return out

  def messages_for(self, chat_id: int) -> list[SentMessage]:
    thread = self.network.bot_chats.get((chat_id, self.bot_id), [])
    return self._bot_thread_messages(chat_id, thread)

  def open_dialog(self, user_id: int, *, first_name: str = "Test") -> None:
    """Seed a private chat as if the user had pressed /start.

    Not a Bot API method: real Telegram has no way for a bot to open a
    dialog. Test sugar, and named as such.
    """
    if user_id not in self.network.users:
      self.network.create_user(id=user_id, first_name=first_name)
    self.network.ensure_private_chat(user_id, self.bot_id)

  @property
  def messages(self) -> list[SentMessage]:
    bot_id = self.bot_id
    out: list[SentMessage] = []
    for (chat_id, owner_id), thread in self.network.bot_chats.items():
      if owner_id == bot_id:
        out.extend(self._bot_thread_messages(chat_id, thread))
    return out

  def queue_error(self, chat_id: int, status_code: int, body: dict[str, Any]) -> None:
    self.network.inject_error(self.token, chat_id, status_code, body)

  def push_update(self, payload: dict[str, Any]) -> int:
    return self.network.push_update(self.token, payload)

  async def wait_acked(self, update_id: int, timeout: float) -> bool:
    return await self.network.wait_acked(self.token, update_id, timeout)
