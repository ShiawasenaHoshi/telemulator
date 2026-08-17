# telemulator

A fake Telegram network you can run in tests and in a browser: a hundred users
and several bots, without a SIM card, BotFather, or real accounts.

- Any bot framework (aiogram, grammY, Telegraf) talks to the fake **Bot API**
  as if it were `api.telegram.org` — point the base URL at it and go.
- A person sits in the **web client** or in a test through the **User API**.
- People write to people, to bots, and in groups and channels.
- State lives in memory or in a SQLite file, and can be snapshotted.

Not affiliated with Telegram.

## Install

    pip install "telemulator @ git+https://github.com/ShiawasenaHoshi/telemulator.git@v0.1.0"

Or run the image:

    docker run -p 8081:8081 ghcr.io/shiawasenahoshi/telemulator:0.1.0

## Use it from a test

```python
from telemulator import TelemulatorServer, UserClient

server = TelemulatorServer()
await server.start()          # loopback HTTP; point your bot at server.url
view = server.state(BOT_TOKEN)
view.open_dialog(900001, first_name="Ann")
user = UserClient(view, 900001)
await user.send("/start")
assert "Menu" in user.screen().text
await server.stop()
```

## Develop

    make setup
    make test
    make web        # the web client on :8081

## What it is not

- Not MTProto: official Telegram apps cannot connect.
- Not a claim of full Bot API coverage. An unknown method answers `404`, like
  Telegram does, and lands in the Admin journal. Coverage grows when a real
  bot hits a hole.

See `docs/` for the design history.
