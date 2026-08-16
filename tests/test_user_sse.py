from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from asgi import session_request
from telemulator import create_app
from telemulator.user_http import events

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


async def test_sse_pushes_bot_message_to_the_human() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    created = await client.post("/user/sessions", json={"user_id": 1})
    token = created.json()["token"]

    queue = app.state.network.subscribe()
    await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "пинг"})
    event = queue.get_nowait()
    app.state.network.unsubscribe(queue)
    assert event["type"] == "message"
    assert event["peer_id"] == 111111111
    assert event["message"]["text"] == "пинг"
    assert event["message"]["chat"]["id"] == 111111111

    # httpx ASGITransport ждёт конец стрима, поэтому ленту читаем из генератора.
    response = await events(session_request(app, token))
    assert response.headers["content-type"].startswith("text/event-stream")
    app.state.network.emit(event)
    payload = None
    try:
      async for chunk in response.body_iterator:
        text = chunk if isinstance(chunk, str) else chunk.decode()
        for line in text.split("\n"):
          if line.startswith("data:"):
            data = json.loads(line[5:].strip())
            if data.get("type") == "message":
              payload = data
              break
        if payload is not None:
          break
    finally:
      await response.body_iterator.aclose()
    assert payload == event


async def test_events_requires_session() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    unauth = await client.get("/user/events")
    assert unauth.status_code == 401


async def test_events_is_event_stream_with_session() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "А"})
    created = await client.post("/user/sessions", json={"user_id": 1})
    token = created.json()["token"]
  response = await events(session_request(app, token))
  try:
    assert response.media_type == "text/event-stream"
  finally:
    await response.body_iterator.aclose()
