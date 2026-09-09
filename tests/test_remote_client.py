from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from telemulator import BotSilentError, RemoteUserClient, create_app

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
USER_ID = 900001


async def _stand(app):
  """A client wired to the app in-process: the transport is ASGI, not a socket."""
  transport = ASGITransport(app=app)
  client = RemoteUserClient("http://tg", USER_ID, TOKEN, transport=transport)
  await client.open()
  return client


async def _reply(app, text: str, *, buttons: list[str] | None = None) -> None:
  """Stand in for the bot: take the update, answer, acknowledge the offset."""
  net = app.state.network
  updates = await net.take_updates(TOKEN, None, 0.0)
  markup = None
  if buttons:
    markup = {"inline_keyboard": [[{"text": b, "callback_data": f"cb:{b}"} for b in buttons]]}
  net.append_bot_message(TOKEN, USER_ID, {"text": text, "reply_markup": markup})
  await net.take_updates(TOKEN, updates[-1]["update_id"] + 1, 0.0)


async def test_send_returns_the_screen_the_bot_drew() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    sending = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add", "Report"])
    screen = await sending

    assert screen.text == "Menu"
    assert screen.inline_labels == ["Add", "Report"]
  finally:
    await client.aclose()


async def test_press_finds_the_button_by_its_label() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    sending = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add", "Report"])
    await sending

    pressing = asyncio.create_task(client.press("Report"))
    await asyncio.sleep(0.05)
    await _reply(app, "Your report")
    screen = await pressing

    assert screen.text == "Your report"
  finally:
    await client.aclose()


async def test_press_callback_reaches_a_button_on_an_older_message() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    first = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add"])
    await first

    second = asyncio.create_task(client.send("something else"))
    await asyncio.sleep(0.05)
    await _reply(app, "Noted")
    await second

    # Telegram leaves old keyboards live; the button two messages back still works.
    pressing = asyncio.create_task(client.press_callback("cb:Add"))
    await asyncio.sleep(0.05)
    await _reply(app, "Adding")
    screen = await pressing

    assert screen.text == "Adding"
  finally:
    await client.aclose()


async def test_a_silent_bot_raises_with_the_stand_attached() -> None:
  app = create_app()
  client = await _stand(app)
  net = app.state.network
  try:
    sending = asyncio.create_task(client.send("/start", timeout=2.0))
    await asyncio.sleep(0.05)
    # Acknowledge without answering: the handler ran and said nothing.
    updates = await net.take_updates(TOKEN, None, 0.0)
    await net.take_updates(TOKEN, updates[-1]["update_id"] + 1, 0.0)

    with pytest.raises(BotSilentError) as caught:
      await sending

    assert "did not reply" in str(caught.value)
  finally:
    await client.aclose()


async def test_photo_and_document_reach_the_bot() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    await client.send_photo(file_id="shot", expect_reply=False)
    await client.send_document(file_id="doc", file_name="receipt.pdf", expect_reply=False)

    net = app.state.network
    updates = await net.take_updates(TOKEN, None, 0.0)
    assert updates[0]["message"]["photo"][-1]["file_id"] == "shot"
    assert updates[1]["message"]["document"]["file_name"] == "receipt.pdf"
  finally:
    await client.aclose()
