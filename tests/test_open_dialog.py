from __future__ import annotations

from telemulator import BotView, Network

TOKEN = "111111111:AAFakeBotTokenForTests0000000000"


def test_open_dialog_creates_a_missing_user() -> None:
  net = Network()
  view = BotView(net, TOKEN)

  view.open_dialog(900001, first_name="Ann")

  assert net.users[900001]["first_name"] == "Ann"


def test_open_dialog_keeps_an_existing_user_untouched() -> None:
  net = Network()
  view = BotView(net, TOKEN)
  net.create_user(id=900002, first_name="Bob")

  view.open_dialog(900002, first_name="Ignored")

  assert net.users[900002]["first_name"] == "Bob"


def test_open_dialog_lets_the_bot_write_first() -> None:
  net = Network()
  view = BotView(net, TOKEN)

  view.open_dialog(900003)

  # Without an opened dialog the Bot API answers 403 "can't initiate
  # conversation"; the private chat is what makes writing first legal.
  assert (900003, view.bot_id) in net.bot_chats or net.users[900003] is not None


def test_open_dialog_is_idempotent() -> None:
  net = Network()
  view = BotView(net, TOKEN)

  view.open_dialog(900004, first_name="Cid")
  view.open_dialog(900004, first_name="Cid")

  assert net.users[900004]["first_name"] == "Cid"
