from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.bot_api import _send_message
from telemulator.client import UserClient
from telemulator.network import Network
from telemulator.view import BotView

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


def _kb(label: str) -> str:
  return json.dumps({"keyboard": [[{"text": label}]], "resize_keyboard": True})


def test_two_bots_keep_separate_reply_keyboards_on_screen() -> None:
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN, first_name="Demo")
  net.create_bot(token=ALERT, first_name="Alert")
  net.ensure_private_chat(9, 111111111)
  net.ensure_private_chat(9, 222222222)
  _send_message(net, TOKEN, {"chat_id": "9", "text": "a", "reply_markup": _kb("Menu")})
  _send_message(net, ALERT, {"chat_id": "9", "text": "b", "reply_markup": _kb("Alert")})
  assert net.reply_keyboard(9, 111111111) == [["Menu"]]
  assert net.reply_keyboard(9, 222222222) == [["Alert"]]
  assert UserClient(BotView(net, TOKEN), 9).screen().reply_keyboard == [["Menu"]]
  assert UserClient(BotView(net, ALERT), 9).screen().reply_keyboard == [["Alert"]]


async def test_user_http_reads_keyboard_by_bot_peer() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 9, "first_name": "Test"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Demo"})
    await client.post("/admin/bots", json={"token": ALERT, "first_name": "Alert"})
    await client.post("/admin/dialogs", json={"user_id": 9, "bot_token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 9, "bot_token": ALERT})
    await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": "9", "text": "a", "reply_markup": _kb("Menu")},
    )
    await client.post(
      f"/bot{ALERT}/sendMessage",
      data={"chat_id": "9", "text": "b", "reply_markup": _kb("Alert")},
    )
    await client.post("/user/sessions", json={"user_id": 9})
    demo = (await client.get("/user/chats/111111111/messages")).json()
    alert = (await client.get("/user/chats/222222222/messages")).json()
    assert demo["reply_keyboard"] == [["Menu"]]
    assert alert["reply_keyboard"] == [["Alert"]]
