from __future__ import annotations

from telemulator.client import UserClient
from telemulator.network import Network
from telemulator.user_api import send_document, send_photo, send_text
from telemulator.view import BotView

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
BOT_ID = 111111111
USER_ID = 9


def _network() -> Network:
  net = Network()
  net.create_user(id=USER_ID, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(USER_ID, BOT_ID)
  return net


def _last(net: Network) -> dict:
  """The newest message in the dialog, read from the store rather than the queue."""
  return net.bot_chats[(USER_ID, BOT_ID)][-1]


async def test_a_reply_to_your_own_message_reaches_the_bot() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "100 food")
  origin = _last(net)

  send_text(net, USER_ID, BOT_ID, "delete", reply_to_message_id=origin["message_id"])

  reply = _last(net)
  assert reply["reply_to_message_id"] == origin["message_id"]
  assert reply["reply_to_message"]["text"] == "100 food"
  # A bot framework reads chat off the quoted message when it parses an update.
  assert reply["reply_to_message"]["chat"]["id"] == USER_ID


async def test_a_reply_to_the_bot_carries_a_chat() -> None:
  net = _network()
  bot_msg = net.append_bot_message(TOKEN, USER_ID, {"text": "Saved"})

  send_text(net, USER_ID, BOT_ID, "delete", reply_to_message_id=bot_msg["message_id"])

  # Bot messages are stored without a chat field; the quote must still have one.
  assert _last(net)["reply_to_message"]["chat"]["id"] == USER_ID


async def test_media_can_reply_too() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "100 food")
  origin_id = _last(net)["message_id"]

  send_photo(net, USER_ID, BOT_ID, file_id="receipt", reply_to_message_id=origin_id)
  assert _last(net)["reply_to_message"]["text"] == "100 food"

  send_document(
    net,
    USER_ID,
    BOT_ID,
    file_id="scan",
    file_name="r.pdf",
    reply_to_message_id=origin_id,
  )
  assert _last(net)["reply_to_message"]["text"] == "100 food"


async def test_an_unknown_reply_target_is_ignored() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "hi", reply_to_message_id=9999)
  assert "reply_to_message" not in _last(net)


async def test_user_client_can_reply() -> None:
  """The in-process client must not be the one left unable to reply."""
  net = _network()
  view = BotView(net, TOKEN)
  user = UserClient(view, USER_ID)

  await user.send("100 food", expect_reply=False)
  origin_id = net.bot_chats[(USER_ID, BOT_ID)][0]["message_id"]

  await user.send("delete", reply_to_message_id=origin_id, expect_reply=False)
  updates = await net.take_updates(TOKEN, None, 0.0)
  reply = updates[-1]["message"]
  assert reply["reply_to_message"]["text"] == "100 food"
  assert reply["reply_to_message"]["chat"]["id"] == USER_ID
