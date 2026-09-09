from __future__ import annotations

from telemulator.network import Network
from telemulator.user_api import send_document, send_photo

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def _network() -> Network:
  net = Network()
  net.create_user(id=9, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(9, 111111111)
  return net


async def test_photo_arrives_as_a_ladder_of_sizes() -> None:
  net = _network()

  send_photo(net, 9, 111111111, file_id="shot")

  update = (await net.take_updates(TOKEN, None, 0.0))[0]
  sizes = update["message"]["photo"]
  assert [s["file_id"] for s in sizes] == ["shot-s", "shot"]
  # The largest size is last, as in real Telegram: a consumer that takes
  # photo[-1] must end up with the big one.
  assert sizes[-1]["width"] == 1280


async def test_photo_bytes_are_fetchable_by_file_id() -> None:
  net = _network()

  send_photo(net, 9, 111111111, file_id="shot")

  assert net.files["shot.bin"] == b"e2e-photo-content"


async def test_document_and_photo_do_not_share_a_payload() -> None:
  net = _network()

  send_document(net, 9, 111111111, file_id="doc")
  send_photo(net, 9, 111111111, file_id="pic")

  assert net.files["doc.bin"] != net.files["pic.bin"]


from httpx import ASGITransport, AsyncClient

from telemulator import create_app


async def _dialog(client: AsyncClient) -> None:
  await client.post("/admin/users", json={"id": 9, "first_name": "Test"})
  await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Demo"})
  await client.post("/admin/dialogs", json={"user_id": 9, "bot_token": TOKEN})
  await client.post("/user/sessions", json={"user_id": 9})


async def test_photo_route_delivers_the_same_ladder_as_the_python_call() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)

    posted = await client.post("/user/chats/111111111/photos", json={"file_id": "shot"})
    assert posted.status_code == 200
    assert isinstance(posted.json()["update_id"], int)

    net = app.state.network
    update = (await net.take_updates(TOKEN, None, 0.0))[0]
    sizes = update["message"]["photo"]
    assert [s["file_id"] for s in sizes] == ["shot-s", "shot"]
    assert net.files["shot.bin"] == b"e2e-photo-content"


async def test_document_route_carries_the_file_name() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)

    posted = await client.post(
      "/user/chats/111111111/documents",
      json={"file_id": "doc", "file_name": "receipt.pdf"},
    )
    assert posted.status_code == 200

    net = app.state.network
    update = (await net.take_updates(TOKEN, None, 0.0))[0]
    assert update["message"]["document"]["file_name"] == "receipt.pdf"


async def test_a_photo_can_answer_an_earlier_message() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)
    sent = await client.post("/user/chats/111111111/messages", json={"text": "100 food"})
    origin_id = sent.json()["message"]["message_id"]

    await client.post(
      "/user/chats/111111111/photos",
      json={"file_id": "receipt", "reply_to_message_id": origin_id},
    )

    net = app.state.network
    updates = await net.take_updates(TOKEN, None, 0.0)
    assert updates[-1]["message"]["reply_to_message"]["text"] == "100 food"


async def test_media_routes_reject_a_peer_that_is_not_a_bot() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await _dialog(client)
    await client.post("/admin/users", json={"id": 10, "first_name": "Other"})

    refused = await client.post("/user/chats/10/photos", json={})
    assert refused.status_code == 400
