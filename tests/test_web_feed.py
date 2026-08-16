from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.client import UserClient
from telemulator.view import BotView

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_http_feed_matches_user_client_screen() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 9, "first_name": "Тест"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 9, "bot_token": TOKEN})
    markup = {
      "inline_keyboard": [
        [{"text": "Да", "callback_data": "yes"}, {"text": "Сайт", "url": "https://example.com"}]
      ]
    }
    await client.post(
      f"/bot{TOKEN}/sendMessage",
      data={"chat_id": "9", "text": "меню", "reply_markup": json.dumps(markup)},
    )
    view = BotView(app.state.network, TOKEN)
    screen = UserClient(view, 9).screen()
    await client.post("/user/sessions", json={"user_id": 9})
    feed = (await client.get("/user/chats/111111111/messages")).json()
    last = feed["messages"][-1]
    assert last["text"] == screen.text == "меню"
    labels = [b["text"] for row in last["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == screen.inline_labels
