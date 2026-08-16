from __future__ import annotations

import json

from aiogram.types import Message, User
from httpx import ASGITransport, AsyncClient

from telemulator import create_app

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_every_implemented_method_answers_with_doc_types() -> None:
  """aiogram models are a machine copy of the docs. If it does not build, it is not drop-in."""
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/users", json={"id": 42, "first_name": "Anna"})
    await client.post("/admin/dialogs", json={"user_id": 42, "bot_token": TOKEN})

    me = (await client.post(f"/bot{TOKEN}/getMe")).json()["result"]
    User.model_validate(me)

    sent = (
      await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "42", "text": "hi"})
    ).json()["result"]
    Message.model_validate(sent)

    photo = (
      await client.post(
        f"/bot{TOKEN}/sendPhoto", data={"chat_id": "42", "photo": "x", "caption": "c"}
      )
    ).json()["result"]
    Message.model_validate(photo)

    document = (
      await client.post(
        f"/bot{TOKEN}/sendDocument",
        data={"chat_id": "42", "document": "x", "file_name": "certificate.pdf"},
      )
    ).json()["result"]
    Message.model_validate(document)

    edited = (
      await client.post(
        f"/bot{TOKEN}/editMessageText",
        data={"chat_id": "42", "message_id": str(sent["message_id"]), "text": "z"},
      )
    ).json()["result"]
    Message.model_validate(edited)

    markup = (
      await client.post(
        f"/bot{TOKEN}/editMessageReplyMarkup",
        data={
          "chat_id": "42",
          "message_id": str(sent["message_id"]),
          "reply_markup": json.dumps({"inline_keyboard": [[{"text": "x", "callback_data": "y"}]]}),
        },
      )
    ).json()["result"]
    Message.model_validate(markup)
    assert markup["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "y"

    cleared = (
      await client.post(
        f"/bot{TOKEN}/editMessageReplyMarkup",
        data={"chat_id": "42", "message_id": str(sent["message_id"])},
      )
    ).json()["result"]
    Message.model_validate(cleared)
    assert "reply_markup" not in cleared
