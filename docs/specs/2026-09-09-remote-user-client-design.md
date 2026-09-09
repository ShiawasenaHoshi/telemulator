# Remote user client

Date: 2026-09-09
Status: draft, not implemented.

## 1. Why

`UserClient` drives a dialog by holding a `BotView` — the emulator's own objects, inside the test's own process. That works whenever the bot under test can be imported: start `TelemulatorServer()` on loopback, point the bot's API base at it, run both halves in one interpreter.

It stops working when the bot cannot be imported. This package requires Python 3.12. A bot pinned to an older interpreter runs in its own container and reaches the fake Bot API over the network — which the published image already serves on `0.0.0.0:8081`. That half of the arrangement is finished. The other half is not: from outside the process there is no way to *be* the user.

The immediate consumer is a pair of Telegram bots on Python 3.8 whose behaviour has to be pinned by tests before they are rewritten. The general case is any bot a test cannot import: another interpreter, another language, a binary.

## 2. What is missing

The User API — the surface the web client already uses — covers most of it. `POST /user/chats/{peer_id}/messages` goes through `user_api.send_text`, so an inbound message gets an honest `message_id` from `_append_inbound` and accepts `reply_to_message_id`. `GET /user/chats/{peer_id}/messages` returns the stored messages together with the reply keyboard, and stored messages carry `reply_markup`, so a screen can be rebuilt in full. Buttons are pressable by `callback_data`. Files are readable.

Four gaps:

0. **A reply to a bot is dropped in a private chat.** `send_text` accepts `reply_to_message_id` and honours it — but only in the branch for groups and channels, where the thread lives in `network.chats`. A dialog with a bot lives in `network.bot_chats`, reaches `_append_inbound`, and the field never arrives. `send_photo` and `send_document` do not take the parameter at all.

   This is a behaviour gap, not a routing one, and it is the one that blocks real work: a bot whose interface is "answer your own message to act on it" cannot be tested at all. The immediate consumer has two such flows — deleting a transaction by replying `delete` to it, and attaching a receipt by replying with a photo.

1. **Media has no route.** `user_api.send_photo` and `send_document` are reachable only in-process. Nothing needs uploading — both functions synthesise the file and register its bytes in `network.files` — so this is an ordinary JSON call, not multipart.

2. **`update_id` is discarded.** `send_text` and `press_callback` return it; `post_message` returns the stored message and `press` returns `query_id`. Neither passes the update on.

3. **Nothing to wait on.** `UserClient._wait` awaits `network.wait_acked(token, update_id, timeout)` — the offset acknowledgement, which arrives *after* the handler returns, when every message has been sent and every write committed. A remote caller cannot reach that.

The third gap is the one that matters. Waiting for a new message to appear is a weaker guarantee: it fires on the bot's first reply, not at the end of the handler, and a test that asserts on the database right after it is racing. Without the ack, a remote client degrades into waiting for silence — precisely what `wait_acked` was built to replace.

## 3. Design

### 3.1 Routes

All additive. Existing fields keep their names and meanings; the web client is untouched.

| Route | Body | Returns |
|---|---|---|
| `POST /user/chats/{peer_id}/photos` | `{"file_id": "user-photo-1", "reply_to_message_id": int \| null}` | `{"update_id": int}` |
| `POST /user/chats/{peer_id}/documents` | `{"file_id": "user-doc-1", "file_name": "certificate.pdf", "reply_to_message_id": int \| null}` | `{"update_id": int}` |
| `POST /user/chats/{peer_id}/messages` | unchanged; `reply_to_message_id` starts working for bot dialogs | `{"message": …, "update_id": int}` |
| `POST …/messages/{message_id}/press` | unchanged | `{"query_id": str, "update_id": int}` |
| `GET /user/chats/{peer_id}/acks/{update_id}?timeout=10` | — | `{"acked": bool}` |

The media routes take defaults from `user_api`, so a caller that wants a plausible photo passes an empty body. Both are private-chat only: they resolve the bot token by `peer_id` through `_token_for_bot` and answer `400` when `peer_id` is not a bot, matching how the underlying functions already fail.

The ack route resolves the same way and awaits `network.wait_acked`. It answers `{"acked": false}` on timeout rather than erroring: a silent bot is a test assertion, not a transport failure, and the client turns it into `BotSilentError` with the state attached.

### 3.2 Client

`RemoteUserClient` in `telemulator/remote.py`, exported from the package. It speaks the routes above over `httpx` — already a dependency — and reuses `Screen`, `SentMessage`, `Button`, `parse_markup` and `BotSilentError` rather than restating them.

Method for method it matches `UserClient`:

| `UserClient` | `RemoteUserClient` |
|---|---|
| `send(text, *, timeout, expect_reply)` | same |
| `press(label, *, timeout)` | same |
| `press_callback(data, *, timeout)` | same |
| `send_photo(*, file_id, timeout, expect_reply)` | same |
| `send_document(*, file_id, file_name, timeout, expect_reply)` | same |
| `send_to(peer_id, text)` | same |
| `screen()`, `messages()` | **`async`** — they cross the wire |

`send`, `send_photo` and `send_document` gain a `reply_to_message_id` argument on **both** clients: gap 0 is fixed in `user_api`, below the client layer, so the in-process client would otherwise be the one left unable to reply.

Making `screen()` and `messages()` awaitable is the one difference the two surfaces cannot hide, and pretending otherwise would mean caching state that another process is changing. Everything else, including `press` resolving a label to `callback_data` from the last message that carries it, behaves as it does in-process.

Construction takes a base URL, a user id and a bot id, and needs the user and the dialog to exist. An explicit `open()` creates the user (`POST /admin/users`), the dialog (`POST /admin/dialogs`) and the session (`POST /user/sessions`), then holds the session token as a bearer. Not a constructor: it does I/O.

`BotSilentError` carries the same dump as in-process — screen text, the tail of the journal, the pending updates — fetched through `GET /admin/journal` and `GET /admin/{token}/messages`. A remote failure should read like a local one.

### 3.3 What stays out

- **SSE.** `GET /user/events` could replace the ack call, but the ack is a single awaited request and the event stream is a second connection to manage. If the ack route proves slow under load, revisit.
- **Real file uploads.** The in-process functions synthesise files; matching them keeps one behaviour, not two.
- **Group and channel driving.** `send_to` covers person-to-person. Membership work already has its own routes; wrapping them belongs to whoever needs it.
- **Making `UserClient` and `RemoteUserClient` a single class behind a flag.** Two small classes that share dataclasses beat one class with two code paths.

## 4. Acceptance

- A reply in a private bot chat reaches the bot with `reply_to_message` and `reply_to_message_id` populated — for text, for a photo and for a document, through both clients.
- The new routes are covered by tests in the style of `tests/test_user_http.py`: ASGI transport, no sockets.
- `RemoteUserClient` has a test that drives a real dialog against `create_app()` over ASGI transport and asserts the same things `tests/test_user_client.py` asserts in-process, including a `BotSilentError` case.
- A photo sent through the new route arrives at the bot as the same ladder of sizes `tests/test_user_media.py` already pins, and its bytes are fetchable by `file_id`.
- `press` finds a button by its label on the current screen; `press_callback` reaches one on an older message, as it does in-process.
- The coverage gate in `make test` stays green.
- The web client keeps working: no existing response field changes name or meaning.

## 5. Release

Additive API, no behaviour change for existing callers: `0.3.0`. Bump `pyproject.toml`, the two version strings in `README.md`, and `IMAGE` in the `Makefile` — which is stale at `0.2.1` while the package is at `0.2.2`, and would otherwise publish the wrong tag.
