from __future__ import annotations

import json

import pytest

from telemulator.bot_api import _send_message
from telemulator.client import BotSilentError, UserClient
from telemulator.network import Network
from telemulator.user_api import press_callback
from telemulator.view import BotView

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT_TOKEN = "222222222:AAFakeAlertTokenForE2ETests0000"


async def test_send_text_to_bot_enqueues_update() -> None:
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(9, 111111111)
  view = BotView(net, TOKEN)
  user = UserClient(view, 9)
  # without a live bot the ack will not come — send with expect_reply=False
  screen = await user.send("/start", expect_reply=False)
  updates = await net.take_updates(TOKEN, None, 0.0)
  assert updates[0]["message"]["text"] == "/start"
  assert updates[0]["message"]["from"]["id"] == 9
  assert screen is not None


def test_botview_messages_expose_parse_mode_from_send_message() -> None:
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(9, 111111111)
  _send_message(
    net, TOKEN, {"chat_id": "9", "text": "<b>hi</b>", "parse_mode": "HTML"}
  )
  assert BotView(net, TOKEN).messages[-1].raw.get("parse_mode") == "HTML"


def _menu(text: str) -> dict[str, str]:
  return json.dumps({"keyboard": [[{"text": text}]], "resize_keyboard": True})


async def test_reply_keyboard_survives_the_next_message() -> None:
  """Telegram keeps the keyboard until it is replaced or removed."""
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(9, 111111111)
  user = UserClient(BotView(net, TOKEN), 9)

  _send_message(net, TOKEN, {"chat_id": "9", "text": "Menu", "reply_markup": _menu("KM estimates")})
  _send_message(net, TOKEN, {"chat_id": "9", "text": "And another message"})
  assert user.screen().reply_keyboard == [["KM estimates"]]
  assert user.screen().message.reply_keyboard is None

  _send_message(
    net,
    TOKEN,
    {"chat_id": "9", "text": "form", "reply_markup": json.dumps({"remove_keyboard": True})},
  )
  assert user.screen().reply_keyboard is None


async def test_press_callback_finds_button_on_older_message() -> None:
  """Telegram does not dismiss inline keyboards: the button lives on the old message."""
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(9, 111111111)
  user = UserClient(BotView(net, TOKEN), 9)

  markup = json.dumps({"inline_keyboard": [[{"text": "Old", "callback_data": "old"}]]})
  _send_message(net, TOKEN, {"chat_id": "9", "text": "first", "reply_markup": markup})
  _send_message(net, TOKEN, {"chat_id": "9", "text": "second"})
  assert user.messages()[-1].buttons == []

  with pytest.raises(BotSilentError):
    await user.press_callback("old", timeout=0.0)

  rec = next(iter(net._callbacks.values()))
  assert rec["message_id"] == 1
  query = net.bots[TOKEN].updates[-1]["callback_query"]
  assert query["data"] == "old"
  assert query["message"]["message_id"] == 1


async def test_press_finds_the_button_when_the_human_talks_to_two_bots() -> None:
  """A person may talk to more than one bot: look up the thread with the message, not the first one."""
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN, first_name="Club")
  net.create_bot(token=ALERT_TOKEN, first_name="Alert")
  net.ensure_private_chat(9, 111111111)
  net.ensure_private_chat(9, 222222222)
  markup = json.dumps({"inline_keyboard": [[{"text": "Yes", "callback_data": "yes"}]]})
  _send_message(net, ALERT_TOKEN, {"chat_id": "9", "text": "from the second", "reply_markup": markup})

  press_callback(net, 9, 9, 1, "yes")

  assert net.bots[ALERT_TOKEN].updates[-1]["callback_query"]["data"] == "yes"
  assert net.bots[TOKEN].updates == []
