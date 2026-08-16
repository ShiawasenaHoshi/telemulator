from __future__ import annotations

from telemulator.client import UserClient
from telemulator.network import Network
from telemulator.view import BotView

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_people_chat_is_invisible_to_bot() -> None:
  net = Network()
  net.create_user(id=1, first_name="A")
  net.create_user(id=2, first_name="B")
  net.create_bot(token=TOKEN)
  a = UserClient(BotView(net, TOKEN), 1, first_name="A")
  await a.send_to(2, "secret")
  assert net.messages_for_peer(1, 2)[0]["text"] == "secret"
  assert await net.take_updates(TOKEN, None, 0.0) == []
