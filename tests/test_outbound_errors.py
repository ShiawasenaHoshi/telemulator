from __future__ import annotations

from httpx import ASGITransport, AsyncClient
import pytest

from telemulator import create_app
from telemulator.errors import CANT_INITIATE, CHAT_NOT_FOUND
from telemulator.network import Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
ALERT = "222222222:AAFakeAlertTokenForE2ETests0000"


async def test_unknown_chat_id_is_400_chat_not_found() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    r = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "x"})
    assert r.status_code == 400
    assert r.json()["description"] == CHAT_NOT_FOUND
    assert r.json()["error_code"] == 400


async def test_human_without_dialog_is_403_cant_initiate() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 42, "first_name": "Тест"})
    await client.post("/admin/bots", json={"token": TOKEN})
    r = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "x"})
    assert r.status_code == 403
    assert r.json()["description"] == CANT_INITIATE


async def test_unknown_negative_without_pair_is_400() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    r = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": "-100999", "text": "x"}
    )
    assert r.status_code == 400
    assert r.json()["description"] == CHAT_NOT_FOUND
    assert -100999 not in app.state.network.chats
    assert (-100999, 111111111) not in app.state.network.bot_chats


async def test_outbound_after_ensure_stays_in_bot_chats_not_chats() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post(
      "/admin/outbound-chats",
      json={"chat_id": -1001234567890, "bot_token": ALERT},
    )
    r = await client.post(
      f"/bot{ALERT}/sendMessage",
      data={"chat_id": "-1001234567890", "text": "alert"},
    )
    assert r.status_code == 200
    net = app.state.network
    assert -1001234567890 not in net.chats
    assert [m["text"] for m in net.bot_chats[(-1001234567890, 222222222)]] == ["alert"]


def test_append_bot_message_does_not_setdefault_negative() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  with pytest.raises(KeyError):
    net.append_bot_message(
      TOKEN, -7, {"chat": {"id": -7, "type": "private"}, "text": "x"}
    )
  assert (-7, 111111111) not in net.bot_chats
