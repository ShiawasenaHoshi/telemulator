from __future__ import annotations

import asyncio

from telemulator.network import Network

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_offset_confirms_every_update_below_it() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  first = net.push_update(TOKEN, {"message": {"text": "/start"}})
  second = net.push_update(TOKEN, {"message": {"text": "/menu"}})

  assert net.bots[TOKEN].acked == 0

  net.ack(TOKEN, second + 1)

  assert net.bots[TOKEN].acked == second
  assert net.bots[TOKEN].acked >= first


async def test_ack_never_moves_backwards() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  update_id = net.push_update(TOKEN, {"message": {"text": "/start"}})

  net.ack(TOKEN, update_id + 1)
  net.ack(TOKEN, update_id)

  assert net.bots[TOKEN].acked == update_id


async def test_wait_acked_returns_when_confirmation_arrives() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  update_id = net.push_update(TOKEN, {"message": {"text": "/start"}})

  async def confirm() -> None:
    await asyncio.sleep(0)
    net.ack(TOKEN, update_id + 1)

  waiter = asyncio.create_task(net.wait_acked(TOKEN, update_id, timeout=5.0))
  await confirm()

  assert await waiter is True


async def test_wait_acked_gives_up_when_the_bot_never_confirms() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  update_id = net.push_update(TOKEN, {"message": {"text": "/start"}})

  assert await net.wait_acked(TOKEN, update_id, timeout=0.05) is False


async def test_get_updates_with_offset_confirms_the_previous_update() -> None:
  net = Network()
  net.create_bot(token=TOKEN)
  update_id = net.push_update(TOKEN, {"message": {"text": "/start"}})

  await net.take_updates(TOKEN, None, 0.0)
  assert net.bots[TOKEN].acked == 0

  await net.take_updates(TOKEN, update_id + 1, 0.0)
  assert net.bots[TOKEN].acked == update_id
