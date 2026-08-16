from __future__ import annotations

import asyncio
from dataclasses import dataclass

from telemulator.user_api import press_callback, send_document, send_text
from telemulator.view import BotView, SentMessage

DEFAULT_TIMEOUT = 10.0
SILENT_DUMP_CALLS = 20


class BotSilentError(AssertionError):
  """The bot said nothing within the timeout — with the stand's state attached."""


@dataclass
class Screen:
  """What the user sees right now: last message plus its keyboards."""

  text: str
  inline_labels: list[str]
  reply_keyboard: list[list[str]] | None
  message: SentMessage

  def button(self, label: str):
    for button in self.message.buttons:
      if button.text == label:
        return button
    return None


class UserClient:
  """One Telegram user talking to the bot through telemulator."""

  def __init__(self, view: BotView, user_id: int, *, first_name: str = "Тест") -> None:
    self._view = view
    self.user_id = user_id
    self.chat_id = user_id
    self.first_name = first_name
    if user_id not in view.network.users:
      view.network.create_user(id=user_id, first_name=first_name)

  def messages(self) -> list[SentMessage]:
    return self._view.messages_for(self.chat_id)

  def screen(self) -> Screen:
    network = self._view.network
    msgs = self.messages()
    if not msgs:
      empty = SentMessage(message_id=0, chat_id=self.chat_id, text="")
      return Screen(
        text="",
        inline_labels=[],
        reply_keyboard=network.reply_keyboard(self.user_id, self._view.bot_id),
        message=empty,
      )
    last = msgs[-1]
    return Screen(
      text=last.text,
      inline_labels=[b.text for b in last.buttons],
      reply_keyboard=network.reply_keyboard(self.user_id, self._view.bot_id),
      message=last,
    )

  async def send_to(self, peer_id: int, text: str) -> None:
    send_text(self._view.network, self.user_id, peer_id, text)

  async def send(
    self, text: str, *, timeout: float = DEFAULT_TIMEOUT, expect_reply: bool = True
  ) -> Screen | None:
    before = len(self.messages())
    update_id = send_text(self._view.network, self.user_id, self._view.bot_id, text)
    if not expect_reply:
      await asyncio.sleep(0.5)
      return self.screen()
    return await self._wait(before, f"send({text!r})", timeout, update_id)

  async def press(self, label: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    screen = self.screen()
    button = screen.button(label)
    if button is None:
      raise AssertionError(
        f"На экране нет кнопки {label!r}. Есть: {screen.inline_labels}. Текст экрана:\n{screen.text}"
      )
    if button.callback_data is None:
      raise AssertionError(f"Кнопка {label!r} — ссылка ({button.url}), нажимать нечего")

    return await self.press_callback(button.callback_data, timeout=timeout)

  async def press_callback(self, data: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    """Нажать кнопку по callback_data, а не по подписи на текущем экране.

    Telegram не гасит инлайн-клавиатуры старых сообщений, поэтому кнопка из
    переписки месячной давности остаётся нажимаемой. Проверять это иначе нечем.
    """
    msgs = self.messages()
    match = next(
      (m for m in reversed(msgs) if any(b.callback_data == data for b in m.buttons)),
      None,
    )
    if match is None:
      screen = self.screen()
      raise AssertionError(
        f"На экране нет кнопки {data!r}. Есть: {screen.inline_labels}. Текст экрана:\n{screen.text}"
      )
    before = len(msgs)
    update_id = press_callback(
      self._view.network, self.user_id, self.chat_id, match.message_id, data
    )
    return await self._wait(before, f"press_callback({data!r})", timeout, update_id)

  async def send_document(
    self,
    *,
    file_id: str = "user-doc-1",
    file_name: str = "справка.pdf",
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
  ) -> Screen | None:
    """User sends a document; the bot will fetch it back via getFile."""
    before = len(self.messages())
    update_id = send_document(
      self._view.network,
      self.user_id,
      self._view.bot_id,
      file_id=file_id,
      file_name=file_name,
    )
    if not expect_reply:
      await asyncio.sleep(0.5)
      return None
    return await self._wait(before, "send_document()", timeout, update_id)

  def _silent_dump(self, action: str) -> str:
    screen = self.screen()
    journal = self._view.network.journal.calls()[-SILENT_DUMP_CALLS:]
    updates = self._view.network.bots[self._view.token].updates
    return (
      f"{action}: бот обработал апдейт и не ответил\n"
      f"screen={screen.text!r}\n"
      f"journal={journal!r}\n"
      f"updates={updates!r}"
    )

  async def _wait(self, before: int, action: str, timeout: float, update_id: int) -> Screen:
    """Ждём, что бот дообработал апдейт, а не что в эфире стало тихо.

    Подтверждение оффсета приходит после возврата обработчика: к этому
    моменту отправлены все сообщения и записаны все изменения в базе.
    Пауза в эфире такой гарантии не давала — она её изображала.
    """
    await self._view.wait_acked(update_id, timeout)
    if len(self.messages()) > before:
      return self.screen()
    raise BotSilentError(self._silent_dump(action))
