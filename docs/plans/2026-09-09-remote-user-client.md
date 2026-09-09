# Remote user client — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a test drive a dialog against a bot that runs in another process, with the same guarantees the in-process `UserClient` gives.

**Architecture:** Two behaviour fixes and three additive routes close the gaps. The first fix is the gate: `/bot{token}/{method}` is declared POST-only and reads `await request.form()`, while pyTelegramBotAPI sends GET for most methods, so a bot built on it gets 405 and never polls. The fix: a reply in a private bot chat currently loses `reply_to_message_id`, because `send_text` only honours it on the groups branch. The routes: media, the `update_id` that `send_text` and `press_callback` already return, and an awaited offset acknowledgement. `RemoteUserClient` then speaks those routes over `httpx` and reuses `Screen`, `SentMessage` and `BotSilentError` instead of restating them. Message parsing moves out of `BotView` into module-level helpers so both clients share one implementation.

**Tech Stack:** FastAPI, httpx, pytest with `asyncio_mode = auto`, ASGI transport in tests (no sockets).

**Spec:** `docs/specs/2026-09-09-remote-user-client-design.md`

## Global Constraints

- Public repository: code, comments, docstrings and commit messages in English. `tests/test_language.py` fails on Cyrillic outside `docs/`.
- Additive only. No existing response field changes name or meaning; the web client must keep working.
- Two-space indentation, `from __future__ import annotations` at the top of every module — the house style in every existing file.
- Reuse `Screen`, `SentMessage`, `Button`, `parse_markup`, `BotSilentError`. Do not restate them for the remote case.
- `httpx` is already a runtime dependency (`pyproject.toml`). Do not add dependencies.
- Tests drive `create_app()` through `httpx.ASGITransport`. No sockets, no live server, no `TelemulatorServer` in these tests.
- `make test` runs the whole suite with a coverage gate. New code needs tests to keep it green.
- Run tests with the project venv: `make setup` once, then `.venv/bin/pytest`.
- Do not push to any remote without the owner asking.

## File structure

| File | Responsibility |
|---|---|
| `telemulator/bot_api.py` | `/bot{token}/{method}` answers GET as well as POST |
| `telemulator/user_api.py` | Replies in private bot chats; `reply_to_message_id` on media |
| `telemulator/client.py` | In-process client gains the same reply argument |
| `telemulator/user_http.py` | New routes; `update_id` added to two responses |
| `telemulator/view.py` | `sent_from_stored` and `is_bot_message` extracted to module level |
| `telemulator/remote.py` | `RemoteUserClient` |
| `telemulator/__init__.py` | Export `RemoteUserClient` |
| `tests/test_bot_api_get.py` | GET on the Bot API |
| `tests/test_private_reply.py` | Replies at the `user_api` level and through `UserClient` |
| `tests/test_user_http.py` | `update_id` and ack route |
| `tests/test_user_media.py` | HTTP media routes next to the existing Python-level tests |
| `tests/test_remote_client.py` | The client, driven over ASGI transport |
| `README.md`, `pyproject.toml`, `Makefile` | Release 0.3.0 |

Order: Task 1 comes first — without it a bot on pyTelegramBotAPI cannot reach the emulator at all, so nothing downstream can be observed. Tasks 2–5 then change the emulator; Task 4 needs Task 3, because the media routes carry the reply parameter Task 3 introduces. Task 6 consumes all of them, Task 7 is last.

---

### Task 1: Accept GET on the Bot API

**Files:**
- Modify: `telemulator/bot_api.py:779-783`
- Test: `tests/test_bot_api_get.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `/bot{token}/{method}` answering GET as well as POST, with arguments read from the query string. Every task after this one depends on it — a bot on pyTelegramBotAPI cannot poll otherwise, so nothing downstream can be observed end to end.

The route is declared `@router.post` and reads its arguments with `await request.form()`. pyTelegramBotAPI 3.7.9 spells `method='post'` in 36 places and leaves the default, GET, everywhere else — `getUpdates` and `sendMessage` included. aiogram POSTs everything, which is why this has never surfaced.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_api_get.py`:

```python
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
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/pytest tests/test_bot_api_get.py -q`
Expected: the two GET tests FAIL with `405`. The POST test passes already and must keep passing.

- [ ] **Step 3: Accept both methods**

In `telemulator/bot_api.py`, replace the decorator and the two argument lines:

```python
@router.api_route("/bot{token}/{method}", methods=["GET", "POST"])
async def call(token: str, method: str, request: Request) -> Response:
  network: Network = request.app.state.network
  # A GET carries its arguments in the query string; pyTelegramBotAPI sends
  # most methods that way. A body, when there is one, is the stronger source.
  params: dict[str, Any] = dict(request.query_params)
  if request.method == "POST":
    form = await request.form()
    params.update({key: form[key] for key in form})
```

The rest of the handler is unchanged: query values arrive as strings, exactly as form values already did.

- [ ] **Step 4: Run the file and the suite**

Run: `.venv/bin/pytest tests/test_bot_api_get.py -q && make test`
Expected: PASS. `tests/test_smoke.py`, `tests/test_limits.py` and `tests/test_journal.py` all drive the same route over POST and are the guard that nothing regressed.

- [ ] **Step 5: Commit**

```bash
git add telemulator/bot_api.py tests/test_bot_api_get.py
git commit -m "fix: answer GET on the Bot API, not only POST

The route read await request.form(), which a GET has no body for.
pyTelegramBotAPI spells method='post' in 36 places and defaults to GET
everywhere else, so a bot built on it got 405 and never polled. aiogram
POSTs everything, which is why this stayed hidden."
```

---

### Task 2: Return `update_id` from the message and press routes

**Files:**
- Modify: `telemulator/user_http.py:191-222`
- Test: `tests/test_user_http.py`

**Interfaces:**
- Consumes: `user_api.send_text` and `user_api._press`, which already produce the update id
- Produces: `POST /user/chats/{peer_id}/messages` → `{"message": …, "update_id": int}`; `POST /user/chats/{peer_id}/messages/{message_id}/press` → `{"query_id": str, "update_id": int}`. Task 5 waits on these ids and Task 6 passes them to the ack route.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_user_http.py`:

```python
async def test_message_and_press_return_the_update_id() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Demo"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})

    sent = await client.post("/user/chats/111111111/messages", json={"text": "hi"})
    assert sent.status_code == 200
    first = sent.json()["update_id"]
    assert isinstance(first, int)

    again = await client.post("/user/chats/111111111/messages", json={"text": "hi again"})
    assert again.json()["update_id"] > first
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/pytest tests/test_user_http.py::test_message_and_press_return_the_update_id -q`
Expected: FAIL with `KeyError: 'update_id'`.

- [ ] **Step 3: Pass the update id through**

In `post_message`, keep the returned id instead of dropping it:

```python
  try:
    update_id = send_text(
      net, viewer_id, peer_id, text, reply_to_message_id=reply_to_message_id
    )
  except KeyError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  except PermissionError as exc:
    raise HTTPException(status_code=403, detail=str(exc)) from exc
  stored = net.thread_for(viewer_id, peer_id)[-1]
  return {"message": message_for_viewer(net, stored, peer_id), "update_id": update_id}
```

In `press`, return both halves of what `_press` already gives:

```python
  try:
    update_id, query_id = _press(net, viewer_id, peer_id, message_id, data)
  except KeyError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  return {"query_id": query_id, "update_id": update_id}
```

- [ ] **Step 4: Run the test and the suite**

Run: `.venv/bin/pytest tests/test_user_http.py tests/test_web_feed.py -q`
Expected: PASS. `test_web_feed.py` is the guard that the web client still reads what it expects.

- [ ] **Step 5: Commit**

```bash
git add telemulator/user_http.py tests/test_user_http.py
git commit -m "feat: return update_id from the user message and press routes

A remote caller needs the id to wait for the offset acknowledgement.
Both values were already produced and thrown away."
```

---

### Task 3: Replies in a private bot chat

**Files:**
- Modify: `telemulator/user_api.py:52-62` and the tail of `send_text`, `send_photo`, `send_document`
- Modify: `telemulator/client.py`
- Test: `tests/test_private_reply.py`

**Interfaces:**
- Consumes: `network.bot_chats`, `network.ensure_private_chat`
- Produces: `_append_inbound(network, user_id, bot_id, fields, *, reply_to_message_id: int | None = None)`; `send_text` honouring the argument for bot dialogs; `send_photo(network, user_id, peer_id, *, file_id, reply_to_message_id=None)`; `send_document(network, user_id, peer_id, *, file_id, file_name, reply_to_message_id=None)`; `UserClient.send`, `send_photo`, `send_document` all accepting `reply_to_message_id`. Task 4 exposes it over HTTP for media; Task 6 uses all of it.

`send_text` already takes `reply_to_message_id`, and honours it — but only where the thread lives in `network.chats`, which is groups and channels. A dialog with a bot lives in `network.bot_chats` and leaves through `_append_inbound`, where the argument never arrives. A bot whose interface is "answer your own message to act on it" is therefore untestable today.

- [ ] **Step 1: Write the failing tests**

`tests/test_private_reply.py`:

```python
from __future__ import annotations

from telemulator.network import Network
from telemulator.user_api import send_document, send_photo, send_text

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
BOT_ID = 111111111
USER_ID = 9


def _network() -> Network:
  net = Network()
  net.create_user(id=USER_ID, first_name="Test")
  net.create_bot(token=TOKEN)
  net.ensure_private_chat(USER_ID, BOT_ID)
  return net


def _last(net: Network) -> dict:
  """The newest message in the dialog, read from the store rather than the queue."""
  return net.bot_chats[(USER_ID, BOT_ID)][-1]


async def test_a_reply_to_your_own_message_reaches_the_bot() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "100 food")
  origin = _last(net)

  send_text(net, USER_ID, BOT_ID, "delete", reply_to_message_id=origin["message_id"])

  reply = _last(net)
  assert reply["reply_to_message_id"] == origin["message_id"]
  assert reply["reply_to_message"]["text"] == "100 food"
  # A bot framework reads chat off the quoted message when it parses an update.
  assert reply["reply_to_message"]["chat"]["id"] == USER_ID


async def test_a_reply_to_the_bot_carries_a_chat() -> None:
  net = _network()
  bot_msg = net.append_bot_message(TOKEN, USER_ID, {"text": "Saved"})

  send_text(net, USER_ID, BOT_ID, "delete", reply_to_message_id=bot_msg["message_id"])

  # Bot messages are stored without a chat field; the quote must still have one.
  assert _last(net)["reply_to_message"]["chat"]["id"] == USER_ID


async def test_media_can_reply_too() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "100 food")
  origin_id = _last(net)["message_id"]

  send_photo(net, USER_ID, BOT_ID, file_id="receipt", reply_to_message_id=origin_id)
  assert _last(net)["reply_to_message"]["text"] == "100 food"

  send_document(
    net,
    USER_ID,
    BOT_ID,
    file_id="scan",
    file_name="r.pdf",
    reply_to_message_id=origin_id,
  )
  assert _last(net)["reply_to_message"]["text"] == "100 food"


async def test_an_unknown_reply_target_is_ignored() -> None:
  net = _network()
  send_text(net, USER_ID, BOT_ID, "hi", reply_to_message_id=9999)
  assert "reply_to_message" not in _last(net)
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/pytest tests/test_private_reply.py -q`
Expected: the first three FAIL — `KeyError: 'reply_to_message'` for text, `TypeError: unexpected keyword argument 'reply_to_message_id'` for photo and document. The fourth passes already, and must keep passing.

- [ ] **Step 3: Carry the reply through `_append_inbound`**

```python
def _append_inbound(
  network: Network,
  user_id: int,
  bot_id: int,
  fields: dict[str, Any],
  *,
  reply_to_message_id: int | None = None,
) -> dict[str, Any]:
  chat = network.ensure_private_chat(user_id, bot_id)
  thread = network.bot_chats[(user_id, bot_id)]
  message = dict(fields)
  if reply_to_message_id is not None:
    origin = next(
      (m for m in thread if m.get("message_id") == reply_to_message_id), None
    )
    if origin is not None:
      quoted = dict(origin)
      # Messages the bot sent are stored without a chat — it is added only on
      # the way out to the feed. In a private chat every message shares one.
      quoted.setdefault("chat", chat)
      message["reply_to_message"] = quoted
      message["reply_to_message_id"] = reply_to_message_id
  message["message_id"] = max((m.get("message_id", 0) for m in thread), default=0) + 1
  message["date"] = int(time.time())
  message.setdefault("chat", chat)
  thread.append(message)
  return message
```

An unknown `reply_to_message_id` stays silent, matching what the groups branch already does.

- [ ] **Step 4: Pass it in from the three senders**

At the tail of `send_text`, where the bot branch calls `_append_inbound`:

```python
  message = _append_inbound(
    network,
    user_id,
    peer_id,
    {"from": dict(user), "text": text},
    reply_to_message_id=reply_to_message_id,
  )
  return network.push_update(token, {"message": message})
```

In `send_document`, add the parameter to the signature and the call:

```python
def send_document(
  network: Network,
  user_id: int,
  peer_id: int,
  *,
  file_id: str = "user-doc-1",
  file_name: str = "certificate.pdf",
  reply_to_message_id: int | None = None,
) -> int:
```

```python
  message = _append_inbound(
    network,
    user_id,
    peer_id,
    {
      "from": dict(user),
      "document": {
        "file_id": file_id,
        "file_unique_id": file_id,
        "file_name": file_name,
        "file_size": 17,
      },
    },
    reply_to_message_id=reply_to_message_id,
  )
```

Do the same for `send_photo`: `reply_to_message_id: int | None = None` in the signature, passed through to its `_append_inbound` call.

- [ ] **Step 5: Run the new tests and the neighbours**

Run: `.venv/bin/pytest tests/test_private_reply.py tests/test_user_media.py tests/test_p2p.py tests/test_groups.py -q`
Expected: PASS. `test_groups.py` guards the branch that already handled replies.

- [ ] **Step 6: Give the in-process client the same argument**

In `telemulator/client.py`, add `reply_to_message_id: int | None = None` to `send`, `send_photo` and `send_document`, and pass it to the matching `user_api` call. For `send`:

```python
  async def send(
    self,
    text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    before = len(self.messages())
    update_id = send_text(
      self._view.network,
      self.user_id,
      self._view.bot_id,
      text,
      reply_to_message_id=reply_to_message_id,
    )
```

The rest of each method is unchanged.

- [ ] **Step 7: Cover the client surface**

Append to `tests/test_private_reply.py`:

```python
async def test_user_client_can_reply(...) -> None:
  """The in-process client must not be the one left unable to reply."""
```

Write it against `TelemulatorServer` in the style of `tests/test_user_client.py`: send a message, take its `message_id` from `net.bot_chats`, send a second with `reply_to_message_id`, assert the bot's update carries the quote. Keep the bot side a stub that acknowledges the offset, exactly as that file already does.

- [ ] **Step 8: Run the suite**

Run: `make test`
Expected: PASS with the coverage gate green.

- [ ] **Step 9: Commit**

```bash
git add telemulator/user_api.py telemulator/client.py tests/test_private_reply.py
git commit -m "fix: carry reply_to_message_id into private bot chats

send_text honoured the field only where the thread lives in chats, which is
groups and channels. A dialog with a bot goes through _append_inbound, and
the reply was dropped; media never took the argument at all.

A bot whose interface is 'answer your own message to act on it' could not
be tested. The quoted message also gets a chat: bot messages are stored
without one, and a framework reads chat off the quote."
```

---

### Task 4: HTTP routes for photos and documents

**Files:**
- Modify: `telemulator/user_http.py`
- Test: `tests/test_user_media.py`

**Interfaces:**
- Consumes: `user_api.send_photo(network, user_id, peer_id, *, file_id, reply_to_message_id)` and `user_api.send_document(network, user_id, peer_id, *, file_id, file_name, reply_to_message_id)` from Task 3, both returning `int`
- Produces: `POST /user/chats/{peer_id}/photos` and `POST /user/chats/{peer_id}/documents`, each taking an optional `reply_to_message_id` and returning `{"update_id": int}`. Task 6 calls them from `send_photo` and `send_document`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_user_media.py`:

```python
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
```

- [ ] **Step 2: Run and watch them fail**

Run: `.venv/bin/pytest tests/test_user_media.py -q`
Expected: the three new tests FAIL with 404 — the routes do not exist. The existing Python-level tests in the file keep passing.

- [ ] **Step 3: Add the routes**

In `telemulator/user_http.py`, extend the import from `telemulator.user_api` with `send_document` and `send_photo`, then add after `post_message`:

```python
@router.post("/user/chats/{peer_id}/photos")
async def post_photo(peer_id: int, request: Request, body: dict[str, Any]) -> dict[str, int]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  file_id = str(body.get("file_id") or "user-photo-1")
  reply_to = body.get("reply_to_message_id")
  try:
    update_id = send_photo(
      net,
      viewer_id,
      peer_id,
      file_id=file_id,
      reply_to_message_id=int(reply_to) if reply_to is not None else None,
    )
  except KeyError as exc:
    raise HTTPException(status_code=400, detail="peer is not a bot") from exc
  return {"update_id": update_id}


@router.post("/user/chats/{peer_id}/documents")
async def post_document(peer_id: int, request: Request, body: dict[str, Any]) -> dict[str, int]:
  net = _net(request)
  viewer_id = _viewer_id(request)
  file_id = str(body.get("file_id") or "user-doc-1")
  file_name = str(body.get("file_name") or "certificate.pdf")
  reply_to = body.get("reply_to_message_id")
  try:
    update_id = send_document(
      net,
      viewer_id,
      peer_id,
      file_id=file_id,
      file_name=file_name,
      reply_to_message_id=int(reply_to) if reply_to is not None else None,
    )
  except KeyError as exc:
    raise HTTPException(status_code=400, detail="peer is not a bot") from exc
  return {"update_id": update_id}
```

Defaults repeat the ones in `user_api` so an empty body still produces a plausible file. `send_photo` and `send_document` raise `KeyError` when `_token_for_bot` finds no bot behind `peer_id`, which is the not-a-bot case.

- [ ] **Step 4: Run the file and the suite**

Run: `.venv/bin/pytest tests/test_user_media.py -q && make test`
Expected: PASS, coverage gate green.

- [ ] **Step 5: Commit**

```bash
git add telemulator/user_http.py tests/test_user_media.py
git commit -m "feat: send photos and documents over the user API

The functions synthesise the file and register its bytes, so this is a
JSON call rather than an upload. Without it a remote driver cannot test
a bot that reads attachments."
```

---

### Task 5: Await the offset acknowledgement over HTTP

**Files:**
- Modify: `telemulator/user_http.py`
- Test: `tests/test_user_http.py`

**Interfaces:**
- Consumes: `network.wait_acked(token, update_id, timeout) -> bool`, and `user_api._token_for_bot(network, bot_id) -> str | None`
- Produces: `GET /user/chats/{peer_id}/acks/{update_id}?timeout=<float>` → `{"acked": bool}`. Task 6 calls it from `_wait`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_user_http.py`:

```python
async def test_ack_route_reports_whether_the_bot_finished_the_update() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/bots", json={"token": TOKEN, "first_name": "Demo"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    await client.post("/user/sessions", json={"user_id": 1})

    sent = await client.post("/user/chats/111111111/messages", json={"text": "hi"})
    update_id = sent.json()["update_id"]

    # Nobody is polling, so the update is never acknowledged.
    pending = await client.get(
      f"/user/chats/111111111/acks/{update_id}", params={"timeout": 0.1}
    )
    assert pending.status_code == 200
    assert pending.json() == {"acked": False}

    # A bot that reads and confirms the offset marks it done.
    net = app.state.network
    await net.take_updates(TOKEN, None, 0.0)
    await net.take_updates(TOKEN, update_id + 1, 0.0)

    done = await client.get(
      f"/user/chats/111111111/acks/{update_id}", params={"timeout": 1.0}
    )
    assert done.json() == {"acked": True}
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest tests/test_user_http.py::test_ack_route_reports_whether_the_bot_finished_the_update -q`
Expected: FAIL with 404.

- [ ] **Step 3: Add the route**

Extend the `telemulator.user_api` import in `user_http.py` with `_token_for_bot`, then add:

```python
@router.get("/user/chats/{peer_id}/acks/{update_id}")
async def wait_ack(
  peer_id: int, update_id: int, request: Request, timeout: float = 10.0
) -> dict[str, bool]:
  """Wait until the bot has finished this update, not until the wire goes quiet.

  A timeout answers False rather than raising: a silent bot is an assertion
  for the caller to make, not a transport failure.
  """
  net = _net(request)
  _viewer_id(request)
  token = _token_for_bot(net, peer_id)
  if token is None:
    raise HTTPException(status_code=400, detail="peer is not a bot")
  return {"acked": await net.wait_acked(token, update_id, timeout)}
```

- [ ] **Step 4: Run the test and the suite**

Run: `.venv/bin/pytest tests/test_user_http.py -q && make test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telemulator/user_http.py tests/test_user_http.py
git commit -m "feat: expose the offset acknowledgement to user API callers

Waiting for a new message fires on the bot's first reply, not at the end
of the handler. In-process UserClient waits on the ack; a remote one had
no way to."
```

---

### Task 6: `RemoteUserClient`

**Files:**
- Modify: `telemulator/view.py:77-96`
- Create: `telemulator/remote.py`
- Test: `tests/test_remote_client.py`

**Interfaces:**
- Consumes: every route from Tasks 1–5; `Screen`, `BotSilentError`, `DEFAULT_TIMEOUT`, `SILENT_DUMP_CALLS` from `telemulator.client`; `SentMessage` and `parse_markup` from `telemulator.view`
- Produces: `RemoteUserClient(base_url: str, user_id: int, bot_token: str, *, first_name: str = "Test", transport: httpx.AsyncBaseTransport | None = None)` with `await open()`, `await send(text, *, timeout, expect_reply, reply_to_message_id)`, `await press(label, *, timeout)`, `await press_callback(data, *, timeout)`, `await send_photo(*, file_id, timeout, expect_reply, reply_to_message_id)`, `await send_document(*, file_id, file_name, timeout, expect_reply, reply_to_message_id)`, `await send_to(peer_id, text)`, `await screen()`, `await messages()`, `await aclose()`. Task 7 exports it.

- [ ] **Step 1: Extract message parsing to module level**

`BotView._sent` and `BotView._bot_thread_messages` hold the only implementation of "stored dict → `SentMessage`" and "is this the bot talking". The remote client needs both and must not fork them.

In `telemulator/view.py`, add above `class BotView`:

```python
def sent_from_stored(chat_id: int, msg: dict[str, Any]) -> SentMessage:
  inline, reply, removed = parse_markup(_as_markup_json(msg.get("reply_markup")))
  return SentMessage(
    message_id=int(msg["message_id"]),
    chat_id=chat_id,
    text=str(msg.get("text") or msg.get("caption") or ""),
    inline_keyboard=inline,
    reply_keyboard=reply,
    reply_keyboard_removed=removed,
    raw=msg,
  )


def is_bot_message(msg: dict[str, Any], bot_id: int) -> bool:
  from_user = msg.get("from") or {}
  return from_user.get("id") == bot_id or bool(from_user.get("is_bot"))
```

and make the methods delegate:

```python
  def _sent(self, chat_id: int, msg: dict[str, Any]) -> SentMessage:
    return sent_from_stored(chat_id, msg)

  def _bot_thread_messages(self, chat_id: int, thread: list[dict[str, Any]]) -> list[SentMessage]:
    return [sent_from_stored(chat_id, m) for m in thread if is_bot_message(m, self.bot_id)]
```

- [ ] **Step 2: Confirm the extraction changed nothing**

Run: `.venv/bin/pytest tests/test_user_client.py tests/test_reply_keyboard.py tests/test_web_feed.py -q`
Expected: PASS, unchanged. This is a refactor; if anything goes red, revert rather than adjust the tests.

- [ ] **Step 3: Write the failing client test**

`tests/test_remote_client.py`:

```python
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from telemulator import BotSilentError, RemoteUserClient, create_app

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"
USER_ID = 900001


async def _stand(app):
  """A client wired to the app in-process: the transport is ASGI, not a socket."""
  transport = ASGITransport(app=app)
  client = RemoteUserClient("http://tg", USER_ID, TOKEN, transport=transport)
  await client.open()
  return client


async def _reply(app, text: str, *, buttons: list[str] | None = None) -> None:
  """Stand in for the bot: take the update, answer, acknowledge the offset."""
  net = app.state.network
  updates = await net.take_updates(TOKEN, None, 0.0)
  markup = None
  if buttons:
    markup = {"inline_keyboard": [[{"text": b, "callback_data": f"cb:{b}"} for b in buttons]]}
  net.append_bot_message(TOKEN, USER_ID, {"text": text, "reply_markup": markup})
  await net.take_updates(TOKEN, updates[-1]["update_id"] + 1, 0.0)


async def test_send_returns_the_screen_the_bot_drew() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    sending = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add", "Report"])
    screen = await sending

    assert screen.text == "Menu"
    assert screen.inline_labels == ["Add", "Report"]
  finally:
    await client.aclose()


async def test_press_finds_the_button_by_its_label() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    sending = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add", "Report"])
    await sending

    pressing = asyncio.create_task(client.press("Report"))
    await asyncio.sleep(0.05)
    await _reply(app, "Your report")
    screen = await pressing

    assert screen.text == "Your report"
  finally:
    await client.aclose()


async def test_press_callback_reaches_a_button_on_an_older_message() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    first = asyncio.create_task(client.send("/start"))
    await asyncio.sleep(0.05)
    await _reply(app, "Menu", buttons=["Add"])
    await first

    second = asyncio.create_task(client.send("something else"))
    await asyncio.sleep(0.05)
    await _reply(app, "Noted")
    await second

    # Telegram leaves old keyboards live; the button two messages back still works.
    pressing = asyncio.create_task(client.press_callback("cb:Add"))
    await asyncio.sleep(0.05)
    await _reply(app, "Adding")
    screen = await pressing

    assert screen.text == "Adding"
  finally:
    await client.aclose()


async def test_a_silent_bot_raises_with_the_stand_attached() -> None:
  app = create_app()
  client = await _stand(app)
  net = app.state.network
  try:
    sending = asyncio.create_task(client.send("/start", timeout=2.0))
    await asyncio.sleep(0.05)
    # Acknowledge without answering: the handler ran and said nothing.
    updates = await net.take_updates(TOKEN, None, 0.0)
    await net.take_updates(TOKEN, updates[-1]["update_id"] + 1, 0.0)

    with pytest.raises(BotSilentError) as caught:
      await sending

    assert "did not reply" in str(caught.value)
  finally:
    await client.aclose()


async def test_photo_and_document_reach_the_bot() -> None:
  app = create_app()
  client = await _stand(app)
  try:
    await client.send_photo(file_id="shot", expect_reply=False)
    await client.send_document(file_id="doc", file_name="receipt.pdf", expect_reply=False)

    net = app.state.network
    updates = await net.take_updates(TOKEN, None, 0.0)
    assert updates[0]["message"]["photo"][-1]["file_id"] == "shot"
    assert updates[1]["message"]["document"]["file_name"] == "receipt.pdf"
  finally:
    await client.aclose()
```

- [ ] **Step 4: Run and watch it fail**

Run: `.venv/bin/pytest tests/test_remote_client.py -q`
Expected: FAIL with `ImportError: cannot import name 'RemoteUserClient'`.

- [ ] **Step 5: Write the client**

`telemulator/remote.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from telemulator.client import DEFAULT_TIMEOUT, SILENT_DUMP_CALLS, BotSilentError, Screen
from telemulator.view import SentMessage, is_bot_message, sent_from_stored


class RemoteUserClient:
  """One Telegram user talking to a bot that runs outside this process.

  Same surface as UserClient, with one unavoidable difference: screen() and
  messages() cross the wire, so they are awaitable. Caching them would mean
  holding state that another process is free to change.
  """

  def __init__(
    self,
    base_url: str,
    user_id: int,
    bot_token: str,
    *,
    first_name: str = "Test",
    transport: httpx.AsyncBaseTransport | None = None,
  ) -> None:
    self.user_id = user_id
    self.chat_id = user_id
    self.bot_token = bot_token
    self.bot_id = int(bot_token.split(":")[0])
    self.first_name = first_name
    self._http = httpx.AsyncClient(base_url=base_url, transport=transport)

  async def open(self) -> RemoteUserClient:
    """Create the user, the dialog and the session. Does I/O, so not __init__."""
    await self._http.post(
      "/admin/users", json={"id": self.user_id, "first_name": self.first_name}
    )
    await self._http.post(
      "/admin/dialogs", json={"user_id": self.user_id, "bot_token": self.bot_token}
    )
    created = await self._http.post("/user/sessions", json={"user_id": self.user_id})
    created.raise_for_status()
    self._http.headers["authorization"] = f"Bearer {created.json()['token']}"
    return self

  async def aclose(self) -> None:
    await self._http.aclose()

  async def _thread(self) -> dict[str, Any]:
    response = await self._http.get(f"/user/chats/{self.bot_id}/messages")
    response.raise_for_status()
    return response.json()

  async def messages(self) -> list[SentMessage]:
    payload = await self._thread()
    return [
      sent_from_stored(self.chat_id, m)
      for m in payload["messages"]
      if is_bot_message(m, self.bot_id)
    ]

  async def screen(self) -> Screen:
    payload = await self._thread()
    keyboard = payload.get("reply_keyboard")
    msgs = [
      sent_from_stored(self.chat_id, m)
      for m in payload["messages"]
      if is_bot_message(m, self.bot_id)
    ]
    if not msgs:
      empty = SentMessage(message_id=0, chat_id=self.chat_id, text="")
      return Screen(text="", inline_labels=[], reply_keyboard=keyboard, message=empty)
    last = msgs[-1]
    return Screen(
      text=last.text,
      inline_labels=[b.text for b in last.buttons],
      reply_keyboard=keyboard,
      message=last,
    )

  async def send_to(self, peer_id: int, text: str) -> None:
    response = await self._http.post(f"/user/chats/{peer_id}/messages", json={"text": text})
    response.raise_for_status()

  async def send(
    self,
    text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/messages",
      json={"text": text, "reply_to_message_id": reply_to_message_id},
    )
    response.raise_for_status()
    update_id = response.json()["update_id"]
    if not expect_reply:
      await asyncio.sleep(0.5)
      return await self.screen()
    return await self._wait(before, f"send({text!r})", timeout, update_id)

  async def press(self, label: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    screen = await self.screen()
    button = screen.button(label)
    if button is None:
      raise AssertionError(
        f"No button {label!r} on the screen. Have: {screen.inline_labels}. "
        f"Screen text:\n{screen.text}"
      )
    if button.callback_data is None:
      raise AssertionError(f"Button {label!r} is a link ({button.url}), nothing to press")
    return await self.press_callback(button.callback_data, timeout=timeout)

  async def press_callback(self, data: str, *, timeout: float = DEFAULT_TIMEOUT) -> Screen:
    """Press by callback_data. Telegram leaves old keyboards live, so a button
    from a month-old message stays pressable; there is no other way to test that."""
    msgs = await self.messages()
    match = next(
      (m for m in reversed(msgs) if any(b.callback_data == data for b in m.buttons)),
      None,
    )
    if match is None:
      screen = await self.screen()
      raise AssertionError(
        f"No button {data!r} on the screen. Have: {screen.inline_labels}. "
        f"Screen text:\n{screen.text}"
      )
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/messages/{match.message_id}/press", json={"data": data}
    )
    response.raise_for_status()
    return await self._wait(
      len(msgs), f"press_callback({data!r})", timeout, response.json()["update_id"]
    )

  async def send_photo(
    self,
    *,
    file_id: str = "user-photo-1",
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    """User sends a photo; the bot will fetch it back via getFile."""
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/photos",
      json={"file_id": file_id, "reply_to_message_id": reply_to_message_id},
    )
    response.raise_for_status()
    if not expect_reply:
      await asyncio.sleep(0.5)
      return None
    return await self._wait(before, "send_photo()", timeout, response.json()["update_id"])

  async def send_document(
    self,
    *,
    file_id: str = "user-doc-1",
    file_name: str = "certificate.pdf",
    timeout: float = DEFAULT_TIMEOUT,
    expect_reply: bool = True,
    reply_to_message_id: int | None = None,
  ) -> Screen | None:
    """User sends a document; the bot will fetch it back via getFile."""
    before = len(await self.messages())
    response = await self._http.post(
      f"/user/chats/{self.bot_id}/documents",
      json={
        "file_id": file_id,
        "file_name": file_name,
        "reply_to_message_id": reply_to_message_id,
      },
    )
    response.raise_for_status()
    if not expect_reply:
      await asyncio.sleep(0.5)
      return None
    return await self._wait(before, "send_document()", timeout, response.json()["update_id"])

  async def _silent_dump(self, action: str) -> str:
    screen = await self.screen()
    journal: Any = []
    updates: Any = []
    try:
      journal = (await self._http.get("/admin/journal")).json()[-SILENT_DUMP_CALLS:]
      updates = (
        await self._http.get(
          f"/admin/{self.bot_token}/messages", params={"chat_id": self.chat_id}
        )
      ).json()
    except httpx.HTTPError:
      pass
    return (
      f"{action}: the bot processed the update and did not reply\n"
      f"screen={screen.text!r}\n"
      f"journal={journal!r}\n"
      f"updates={updates!r}"
    )

  async def _wait(self, before: int, action: str, timeout: float, update_id: int) -> Screen:
    await self._http.get(
      f"/user/chats/{self.bot_id}/acks/{update_id}", params={"timeout": timeout}
    )
    if len(await self.messages()) > before:
      return await self.screen()
    raise BotSilentError(await self._silent_dump(action))
```

- [ ] **Step 6: Run the client tests**

Run: `.venv/bin/pytest tests/test_remote_client.py -q`
Expected: PASS.

If `httpx` rejects `timeout` as a float query parameter, pass `params={"timeout": str(timeout)}` — the route parses it back. Do not change the route signature.

- [ ] **Step 7: Run the whole suite**

Run: `make test`
Expected: PASS with the coverage gate green.

- [ ] **Step 8: Commit**

```bash
git add telemulator/view.py telemulator/remote.py tests/test_remote_client.py
git commit -m "feat: RemoteUserClient for bots that cannot be imported

A bot pinned to an older interpreter runs in its own container and already
talks to the fake Bot API over HTTP. This is the other half: being the user
from outside the process, with the same ack-based wait as in-process.

Message parsing moves to module level so both clients share one implementation."
```

---

### Task 7: Export, document, release 0.3.0

**Files:**
- Modify: `telemulator/__init__.py`
- Modify: `README.md`
- Modify: `pyproject.toml:7`
- Modify: `Makefile:5`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `RemoteUserClient` from Task 6
- Produces: `from telemulator import RemoteUserClient`; image tag `ghcr.io/shiawasenahoshi/telemulator/emulator:0.3.0`

- [ ] **Step 1: Write the failing export test**

Append to `tests/test_app.py`:

```python
def test_remote_client_is_exported() -> None:
  import telemulator

  assert "RemoteUserClient" in telemulator.__all__
  assert telemulator.RemoteUserClient is not None
```

- [ ] **Step 2: Run and watch it fail**

Run: `.venv/bin/pytest tests/test_app.py::test_remote_client_is_exported -q`
Expected: FAIL — the name is not in `__all__`.

- [ ] **Step 3: Export it**

In `telemulator/__init__.py`, add the import and the `__all__` entry, keeping both lists alphabetical:

```python
from telemulator.remote import RemoteUserClient
```

```python
  "Network",
  "RemoteUserClient",
  "Screen",
```

- [ ] **Step 4: Document it in the README**

After the "Use it from a test" example, add a section:

````markdown
## When the bot cannot be imported

The example above runs the bot in the test's own process. A bot on another
interpreter — or another language — runs elsewhere and reaches the emulator
over HTTP. Point it at a running server, and drive the person with
`RemoteUserClient`:

```python
from telemulator import RemoteUserClient

user = await RemoteUserClient("http://localhost:8081", 900001, BOT_TOKEN).open()
screen = await user.send("/start")
assert "Menu" in screen.text
await user.aclose()
```

Same methods as `UserClient`, and the same wait: `send` returns once the bot
has acknowledged the update, not once the wire falls quiet. `screen()` and
`messages()` are awaitable here, because they cross the wire.
````

- [ ] **Step 5: Bump the version in all three places**

`pyproject.toml`: `version = "0.3.0"`.

`README.md`: both `0.2.2` occurrences — the `pip install` line and the `docker run` line.

`Makefile`: `IMAGE := ghcr.io/shiawasenahoshi/telemulator/emulator:0.3.0`. It currently reads `0.2.1` while the package is at `0.2.2`, so `make image` has been building a tag one release behind; fix it to the new version rather than carrying the drift forward.

- [ ] **Step 6: Verify**

```bash
make test
grep -rn "0\.2\.[12]" README.md Makefile pyproject.toml
```

Expected: suite green; the grep prints nothing.

- [ ] **Step 7: Commit**

```bash
git add telemulator/__init__.py README.md pyproject.toml Makefile tests/test_app.py
git commit -m "chore: 0.3.0

Export RemoteUserClient and document driving a bot from outside the process.
Makefile IMAGE was a release behind and is realigned."
```

- [ ] **Step 8: Tag — only when the owner asks**

```bash
git tag v0.3.0
```

Do not push the tag or the branch without being asked. Downstream projects can pin this tag in their test requirements.

---

## Acceptance

```text
routes:   POST /user/chats/{peer}/photos and /documents return update_id
          POST /user/chats/{peer}/messages returns message + update_id
          POST .../messages/{id}/press returns query_id + update_id
          GET  /user/chats/{peer}/acks/{id}?timeout= returns {"acked": bool}
client:   RemoteUserClient with send, press, press_callback, send_photo,
          send_document, send_to, screen, messages, open, aclose
          BotSilentError carries screen, journal and pending updates
shared:   sent_from_stored and is_bot_message are the only parsing path,
          used by both BotView and RemoteUserClient
tests:    ASGI transport, no sockets; make test green with coverage
web:      test_web_feed.py unchanged and passing — no field renamed
release:  0.3.0 in pyproject, README and Makefile; tag not pushed
```
