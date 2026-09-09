from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from telemulator import create_app

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
BOT_ID = 111111111
USER_ID = 9


async def _dialog(client: AsyncClient) -> None:
  await client.post("/admin/users", json={"id": USER_ID, "first_name": "Test"})
  await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Demo"})
  await client.post("/admin/dialogs", json={"user_id": USER_ID, "bot_token": TOKEN})


async def test_get_updates_answers_a_get_request() -> None:
  """pyTelegramBotAPI sends GET for most methods, getUpdates among them."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)

    answered = await client.get(f"/bot{TOKEN}/getUpdates", params={"timeout": "0"})

    assert answered.status_code == 200
    assert answered.json()["ok"] is True


async def test_send_message_over_get_reads_the_query_string() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)

    sent = await client.get(
      f"/bot{TOKEN}/sendMessage", params={"chat_id": str(USER_ID), "text": "hi"}
    )

    assert sent.status_code == 200
    assert sent.json()["ok"] is True
    assert app.state.network.bot_chats[(USER_ID, BOT_ID)][-1]["text"] == "hi"


async def test_a_post_body_still_wins_over_the_query_string() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)

    await client.post(
      f"/bot{TOKEN}/sendMessage?text=from-query",
      data={"chat_id": str(USER_ID), "text": "from-body"},
    )

    assert app.state.network.bot_chats[(USER_ID, BOT_ID)][-1]["text"] == "from-body"
