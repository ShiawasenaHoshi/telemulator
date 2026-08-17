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
