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

    pip install "telemulator @ git+https://github.com/ShiawasenaHoshi/telemulator.git@v0.2.0"

Or run the image:

    docker run -p 8081:8081 ghcr.io/shiawasenahoshi/telemulator:0.2.0

## Use it from a test

The emulator is one half of the setup; your own bot is the other. Start the
server, point the bot's API base URL at `server.url`, and let it poll — then
drive a human and assert on what they see.

```python
from telemulator import TelemulatorServer, UserClient

BOT_TOKEN = "111111111:AAFakeBotTokenForTests0000000000"

server = TelemulatorServer()
await server.start()                      # loopback HTTP on a free port

# Your bot runs here, with its API base URL set to server.url and polling
# getUpdates. Nothing below will get an answer until it does.

view = server.state(BOT_TOKEN)
view.open_dialog(900001, first_name="Ann")   # as if the user had pressed /start
user = UserClient(view, 900001)

await user.send("/start")                 # waits for the bot to reply
assert "Menu" in user.screen().text

await server.stop()
```

`press("<label>")` pushes an inline button by the label on the current screen.
Both it and `send` wait for the bot to answer and raise `BotSilentError` with
the screen, the Bot API call journal, and the pending update queue if it
doesn't — a silent bot fails loudly instead of timing out into a bare
assertion. Pass `expect_reply=False` to `send` when no answer is expected.

Two people can talk with no bot involved at all: `await ann.send_to(bob.user_id,
"hi")` puts the message in both feeds.

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
