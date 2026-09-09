from __future__ import annotations

import asyncio
from typing import Any

import httpx

from telemulator.client import DEFAULT_TIMEOUT, SILENT_DUMP_CALLS, BotSilentError, Screen
from telemulator.view import SentMessage, is_bot_message, sent_from_stored


class RemoteUserClient:
  """One Telegram user talking to a bot that runs outside this process.

  Same surface as UserClient, with one unavoidable difference: screen() and
  messages() cross the wire, so they are awaitable. Caching them would mean
  holding state that another process is free to change.
  """

  def __init__(
    self,
    base_url: str,
    user_id: int,
    bot_token: str,
    *,
    first_name: str = "Test",
    transport: httpx.AsyncBaseTransport | None = None,
  ) -> None:
    self.user_id = user_id
    self.chat_id = user_id
    self.bot_token = bot_token
    self.bot_id = int(bot_token.split(":")[0])
    self.first_name = first_name
    self._http = httpx.AsyncClient(base_url=base_url, transport=transport)

  async def open(self) -> RemoteUserClient:
    """Create the user, the dialog and the session. Does I/O, so not __init__."""
    await self._http.post(
      "/admin/users", json={"id": self.user_id, "first_name": self.first_name}
    )
    await self._http.post(
      "/admin/dialogs", json={"user_id": self.user_id, "bot_token": self.bot_token}
    )
    created = await self._http.post("/user/sessions", json={"user_id": self.user_id})
    created.raise_for_status()
    self._http.headers["authorization"] = f"Bearer {created.json()['token']}"
    return self

  async def aclose(self) -> None:
    await self._http.aclose()

  async def _thread(self) -> dict[str, Any]:
    response = await self._http.get(f"/user/chats/{self.bot_id}/messages")
    response.raise_for_status()
    return response.json()

  async def messages(self) -> list[SentMessage]:
    payload = await self._thread()
    return [
      sent_from_stored(self.chat_id, m)
      for m in payload["messages"]
      if is_bot_message(m, self.bot_id)
    ]

  async def screen(self) -> Screen:
    payload = await self._thread()
    keyboard = payload.get("reply_keyboard")
    msgs = [
      sent_from_stored(self.chat_id, m)
      for m in payload["messages"]
      if is_bot_message(m, self.bot_id)
    ]
    if not msgs:
      empty = SentMessage(message_id=0, chat_id=self.chat_id, text="")
      return Screen(text="", inline_labels=[], reply_keyboard=keyboard, message=empty)
    last = msgs[-1]
    return Screen(
      text=last.text,
      inline_labels=[b.text for b in last.buttons],
      reply_keyboard=keyboard,
      message=last,
    )

  async def send_to(self, peer_id: int, text: str) -> None:
    response = await self._http.post(f"/user/chats/{peer_id}/messages", json={"text": text})
    response.raise_for_status()

  async def send(
    self,
    text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/messages",
      json={"text": text, "reply_to_message_id": reply_to_message_id},
    )
    response.raise_for_status()
    update_id = response.json()["update_id"]
    if not expect_reply:
      await asyncio.sleep(0.5)
      return await self.screen()
    return await self._wait(before, f"send({text!r})", timeout, update_id)

  async def press(self, label: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    screen = await self.screen()
    button = screen.button(label)
    if button is None:
      raise AssertionError(
        f"No button {label!r} on the screen. Have: {screen.inline_labels}. "
        f"Screen text:\n{screen.text}"
      )
    if button.callback_data is None:
      raise AssertionError(f"Button {label!r} is a link ({button.url}), nothing to press")
    return await self.press_callback(button.callback_data, timeout=timeout)

  async def press_callback(self, data: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    """Press by callback_data. Telegram leaves old keyboards live, so a button
    from a month-old message stays pressable; there is no other way to test that."""
    msgs = await self.messages()
    match = next(
      (m for m in reversed(msgs) if any(b.callback_data == data for b in m.buttons)),
      None,
    )
    if match is None:
      screen = await self.screen()
      raise AssertionError(
        f"No button {data!r} on the screen. Have: {screen.inline_labels}. "
        f"Screen text:\n{screen.text}"
      )
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/messages/{match.message_id}/press", json={"data": data}
    )
    response.raise_for_status()
    return await self._wait(
      len(msgs), f"press_callback({data!r})", timeout, response.json()["update_id"]
    )

  async def send_photo(
    self,
    *,
    file_id: str = "user-photo-1",
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    """User sends a photo; the bot will fetch it back via getFile."""
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/photos",
      json={"file_id": file_id, "reply_to_message_id": reply_to_message_id},
    )
    response.raise_for_status()
    if not expect_reply:
      await asyncio.sleep(0.5)
      return None
    return await self._wait(before, "send_photo()", timeout, response.json()["update_id"])

  async def send_document(
    self,
    *,
    file_id: str = "user-doc-1",
    file_name: str = "certificate.pdf",
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    """User sends a document; the bot will fetch it back via getFile."""
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/documents",
      json={
        "file_id": file_id,
        "file_name": file_name,
        "reply_to_message_id": reply_to_message_id,
      },
    )
    response.raise_for_status()
    if not expect_reply:
      await asyncio.sleep(0.5)
      return None
    return await self._wait(before, "send_document()", timeout, response.json()["update_id"])

  async def _silent_dump(self, action: str) -> str:
    screen = await self.screen()
    journal: Any = []
    updates: Any = []
    try:
      journal = (await self._http.get("/admin/journal")).json()["calls"][-SILENT_DUMP_CALLS:]
      updates = (
        await self._http.get(
          f"/admin/{self.bot_token}/messages", params={"chat_id": self.chat_id}
        )
      ).json()
    except httpx.HTTPError:
      pass
    return (
      f"{action}: the bot processed the update and did not reply\n"
      f"screen={screen.text!r}\n"
      f"journal={journal!r}\n"
      f"updates={updates!r}"
    )

  async def _wait(self, before: int, action: str, timeout: float, update_id: int) -> Screen:
    await self._http.get(
      f"/user/chats/{self.bot_id}/acks/{update_id}", params={"timeout": timeout}
    )
    if len(await self.messages()) > before:
      return await self.screen()
    raise BotSilentError(await self._silent_dump(action))
