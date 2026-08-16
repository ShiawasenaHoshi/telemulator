from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.limits import RateLimiter

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def test_per_chat_and_global_buckets() -> None:
  now = [0.0]
  lim = RateLimiter(clock=lambda: now[0], per_chat_per_sec=1.0, global_per_sec=30.0)
  assert lim.check(1, 10) is None
  lim.record(1, 10)
  assert lim.check(1, 10) == 1
  assert lim.check(1, 11) is None  # другой чат
  for chat in range(100, 130):
    lim.record(1, chat)
  assert lim.check(1, 200) == 1  # глобальные 30/с


async def test_profile_off_by_default_allows_burst() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    for _ in range(5):
      r = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "x"})
      assert r.status_code == 200


async def test_faq_profile_429_on_same_chat_burst() -> None:
  now = [0.0]
  app = create_app()
  app.state.limiter = RateLimiter(clock=lambda: now[0])
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})

    first = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "a"})
    assert first.status_code == 200

    second = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "b"})
    assert second.status_code == 429
    assert second.json()["parameters"]["retry_after"] >= 1

    now[0] += 1.0
    third = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "c"})
    assert third.status_code == 200


async def test_admin_limits_installs_and_clears_the_profile() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    assert app.state.limiter is None
    await client.post("/admin/limits", json={"profile": "telegram-faq"})
    assert app.state.limiter is not None
    await client.post("/admin/limits", json={"profile": None})
    assert app.state.limiter is None
    bad = await client.post("/admin/limits", json={"profile": "выдумка"})
    assert bad.status_code == 400


def test_group_bucket_20_per_minute_not_private() -> None:
  now = [0.0]
  lim = RateLimiter(clock=lambda: now[0])
  bot_id, chat_id = 1, -1000000000001
  for _ in range(20):
    assert lim.check(bot_id, chat_id, group_chat=True) is None
    lim.record(bot_id, chat_id, group_chat=True)
    now[0] += 1.01
  retry = lim.check(bot_id, chat_id, group_chat=True)
  assert retry is not None
  assert retry >= 1
  assert lim.check(bot_id, 10, group_chat=False) is None
  lim.record(bot_id, 10, group_chat=False)
  now[0] += 1.01
  assert lim.check(bot_id, 10, group_chat=False) is None


async def test_faq_20_per_min_hits_chatrecord_not_outbound() -> None:
  now = [0.0]
  app = create_app()
  app.state.limiter = RateLimiter(clock=lambda: now[0])
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})
    chat = (
      await client.post("/user/chats", json={"type": "supergroup", "title": "S"})
    ).json()["chat"]
    app.state.network.add_member(chat["id"], 111111111, actor_id=1)
    for _ in range(20):
      r = await client.post(
        f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "g"}
      )
      assert r.status_code == 200
      now[0] += 1.01
    limited = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": str(chat["id"]), "text": "g"}
    )
    assert limited.status_code == 429
    assert "retry after" in limited.json()["description"]
    assert limited.json()["parameters"]["retry_after"] >= 1
    await client.post("/admin/users", json={"id": 2, "first_name": "Б"})
    await client.post("/admin/dialogs", json={"user_id": 2, "bot_token": TOKEN})
    now[0] += 1.01
    priv = await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "2", "text": "p"})
    assert priv.status_code == 200
    await client.post(
      "/admin/outbound-chats",
      json={"chat_id": -1001234567890, "bot_token": TOKEN},
    )
    now[0] += 1.01
    out = await client.post(
      f"/bot{TOKEN}/sendMessage", data={"chat_id": "-1001234567890", "text": "o"}
    )
    assert out.status_code == 200
