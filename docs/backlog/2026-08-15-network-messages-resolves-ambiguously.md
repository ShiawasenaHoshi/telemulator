# `Network.messages()` breaks once a person has a second chat

Found 2026-08-15, while reviewing the first wave of the emulator, back when it
still lived inside another repository. Carried over on 2026-08-18 because the
code it describes came along unchanged.

## What is wrong

`Network.messages(chat_id)` (`telemulator/network.py:624`) resolves a thread
from a bare `chat_id`, and that id is ambiguous by construction: a person's id
is both their chat with a bot and the same person as a peer in a chat with
somebody else. With two chats open, `_find_thread` raises `ValueError` — and
`tests/test_network.py` pins that as expected behaviour.

## How to reproduce

Give a person two chats — one with a bot, one with another person — and call
`Network.messages(chat_id)` with the bare id.

## Why it matters

The product targets a hundred users, each with several correspondents. Any
consumer that reads a feed through this method hits the ambiguity on its first
screen.

## Already solved, in part

The design half is done. The user-facing API went the other way:
`Network.thread_for(viewer_id, peer_id)` and `Network.chats_for(viewer_id)`
resolve by the pair "who is looking × at whom", and `telemulator/user_http.py`
goes through them exclusively.

What remains is not a design decision but a cleanup: `messages(chat_id)`
survives next to them, so two roads lead to the same data and one of them is
ambiguous. Drop it in favour of `thread_for`, after checking the remaining
callers in `tests/test_network.py`.

## Estimate

Half an hour, whenever hands reach it. No urgency: the ambiguous road is no
longer the one the API takes.
