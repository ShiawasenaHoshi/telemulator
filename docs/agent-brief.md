# Agent brief

What an agent needs before touching this repository. Written by reading the
code and running the commands, not by copying the README; every command below
was executed on 2026-08-18 and the output is quoted where it matters.

## 1. What this is

A fake Telegram network that runs in tests and in a browser: a Bot API a real
framework can poll, a User API a person (or a test) drives, and an Admin API
that inspects and seeds state — all over one in-memory model. It exists so bot
scenarios can be tested end to end without a SIM card, BotFather, or real
accounts. Its first consumer is the private project it was extracted from; the
package is public and framework-agnostic (aiogram is only a test dependency).
It is not MTProto and makes no claim of full Bot API coverage.

## 2. Stack and versions

- Python `>=3.12`, single package `telemulator`, setuptools build backend,
  version `0.2.1` in `pyproject.toml` (also hardcoded in `Makefile:IMAGE` and
  in the README install lines — three places, keep them in step).
- Runtime dependencies: `fastapi>=0.110`, `uvicorn[standard]>=0.27`,
  `httpx>=0.27`, `python-multipart>=0.0.9`. Nothing else — no database, no
  ORM; SQLite persistence goes through the stdlib `sqlite3`.
- Dev extras (`.[dev]`): `aiogram>=3.4`, `pytest>=8`, `pytest-asyncio`,
  `pytest-cov`, `pytest-xdist`. `aiogram` is deliberately test-only: the
  contract suite feeds our JSON to a real bot framework.
- No lock file. Versions are floors, so a fresh `make setup` resolves to
  whatever is current; the run behind this brief resolved fastapi 0.141.1,
  uvicorn 0.52.3, aiogram 3.30.0, pytest 9.1.1 under CPython 3.12.13.
- No linter and no type checker — not in the Makefile, not in CI. The only
  static step anywhere is `python -m compileall -q telemulator` in CI.

## 3. Layout

```
telemulator/        the package — 3.7k lines of Python, flat, no sub-packages
  app.py            create_app(): mounts the three routers, /health, web/
  server.py         TelemulatorServer — the same app on a loopback port
  network.py        the single source of truth: users, bots, chats, threads,
                    membership, update queues, dump()/load()   (994 lines)
  bot_api.py        POST /bot{token}/{method} + GET /file/bot{token}/{path}
  user_api.py       user-side actions as functions (send, press, media)
  user_http.py      the same actions over HTTP: /user/* incl. SSE /user/events
  admin_api.py      /admin/*: reset, seed users/bots/chats, journal, snapshot
  view.py           BotView / SentMessage / Button — the test-facing read model
  client.py         UserClient / Screen — send(), press(), BotSilentError
  chats.py          ChatRecord, Member, ids, membership JSON
  privacy.py        group/channel delivery rules (privacy mode)
  limits.py         rate limiting profiles -> 429 retry_after
  journal.py        every Bot API call, incl. unknown methods
  errors.py         Telegram-shaped error bodies
  store.py          MemoryStore / SqliteStore (kv table, one JSON blob)
  catalog.py        catalog.json — known Bot API methods, ok/unimplemented/unknown
  web/              the browser client (static app.js/index.html/style.css)
tests/              33 modules, one flat contract suite; asgi.py and
                    repo_files.py are helpers, not tests
docs/               design history (Russian, imported) + backlog/ + this file
```

Boundaries worth respecting: `network.py` owns state and every mutation goes
through it; the three API modules are thin translations of HTTP into `Network`
calls; `view.py`/`client.py` are the only things test authors are meant to
import. The public surface is exactly `telemulator/__init__.py.__all__`.

## 4. Running it locally

```bash
make setup   # python3.12 -m venv .venv && pip install -e ".[dev]"
make test    # the whole suite with the coverage gate
make web     # the web client on 127.0.0.1:8081
make image   # docker build -t ghcr.io/shiawasenahoshi/telemulator/emulator:0.2.1 .
```

All four are real; `setup`, `test` and `web` were run for this brief (`image`
was not — it needs a Docker daemon and builds nothing this brief depends on).
`make web` serves the browser client at `/`, the health probe at `/health`,
and the Bot API at `/bot<token>/<method>`; verified against a live process:

```
GET  /health                                   -> 200 {"status":"ok"}
GET  /                                         -> 200 (the web client)
POST /bot111111111:AAFake.../getMe             -> 200 {"ok":true,...}
```

**No environment variables at all.** There is no config module and nothing
reads `os.environ`; everything is a `create_app()` keyword — `network`,
`limits_profile`, `sqlite_path`. State is in memory unless `sqlite_path` is
passed. External dependencies: none at runtime (Docker only for `make image`,
a network only for `pip install`).

In-process use is `TelemulatorServer()`: `await server.start()` binds a free
loopback port, `server.url` is what the bot's API base URL must point at, and
`server.state(token)` returns the `BotView` tests read. The README example is
accurate but incomplete on purpose — nothing answers until your own bot is
running against `server.url` and polling `getUpdates`.

## 5. Tests and gates

One command, one suite: `make test` = `pytest --cov=telemulator --cov-branch
-n 4 -q`. Measured here:

```
190 passed in 15.02s        (wall clock 16.0s, 4 xdist workers)
TOTAL  2157 stmts  221 miss  718 branch  116 partial  87%
Required test coverage of 87.0% reached. Total coverage: 87.10%
```

Green means: 190 passing and total coverage `>=87` (`fail_under` in
`pyproject.toml`). The margin is 0.1 pp — a single uncovered new branch turns
the build red, which is intended. The gate is set at the level that was
measured at extraction time, not at an aspiration; raising it is its own task.

Four tests are guards rather than behaviour, and they are the ones most likely
to fail on an innocent-looking change:

- `tests/test_origin.py` — no trace of the private project this was extracted
  from may appear in any tracked text file, docs included (the forbidden names
  are base64 in the test so the guard does not itself republish them). Two
  further words are banned outside `docs/`.
- `tests/test_language.py` — no Cyrillic outside `docs/`. The product speaks
  English; `docs/` is an as-is Russian import.
- `tests/test_wheel.py` — builds a real wheel in a temp dir and asserts
  `catalog.json` and `web/` are inside it.
- `tests/test_catalog.py` — the method catalog stays consistent.

Both guards read `git ls-files`, so untracked scratch cannot fail them and an
ignored file cannot hide from them.

CI (`.github/workflows/ci.yml`) runs three jobs on push to `main`, on tags
`v*`, and on PRs into `main`: `test` (compileall + the same pytest line with
`-n 2`), `wheel` (build, install into a clean venv, import and call
`create_app()`), and `docker` (push only, publishes to
`ghcr.io/<owner>/<repo>/emulator`).

## 6. Branches and worktrees

Small and conventional, and the whole history is short — the repository was
created on 2026-08-18 by extracting the package from a private project.

- `main` is the trunk and is protected by habit, not by a rule: work happens
  on a topic branch named `<kind>/<slug>` (`docs/backlog-from-origin` is the
  only one so far) and lands through a GitHub PR. PR #1 was merged that way.
- Tags `v0.1.0`, `v0.2.0`, `v0.2.1` mark releases; downstream pins the package
  by tag, so a tag is a published contract.
- Commit subjects: lowercase, English, conventional prefix — `feat:`, `fix:`,
  `test:`, `docs:`, `ci:`, `build:`, `chore:`. One concern per commit.
- There are no long-lived worktrees here. Multica runs a task in its own
  worktree on a branch like `agent/<agent>/<id>`; that is the runtime's
  convention, not the repository's, and such a branch should reach `main`
  through a PR like any other.
- Before starting: the owner's checkout may sit on a topic branch (it was on
  `docs/backlog-from-origin`) while `origin/main` is already ahead of the
  local `main` ref. Branch from `origin/main` after a fetch, not from the
  local one.

## 7. Danger zones

Nothing here handles money or personal data, so the risks are about what
leaves the repository.

- `tests/test_origin.py` and `tests/test_language.py` — do not weaken, skip,
  or add exceptions to these without the owner. They are the reason this
  package could be published at all. If a change makes one fail, the change is
  wrong, not the guard.
- `docs/` — an imported historical record. It stays in Russian and is not
  rewritten to match current code; corrections belong in new documents.
- `pyproject.toml` `[tool.setuptools.package-data]` — `catalog.json` and
  `web/` are loaded relative to `__file__`. Drop them and the wheel imports
  fine and dies on first use; `tests/test_wheel.py` and the CI `wheel` job are
  the only things standing between that and a release.
- `[tool.coverage.report] fail_under` — lowering it to make a build pass is a
  silent regression of the only quality gate in the project.
- Version strings in `pyproject.toml`, `Makefile`, `README.md`, and the image
  tag in the consumer's compose file move together. A tag published without
  the version bumped is a broken pin downstream.
- `.github/workflows/ci.yml` `IMAGE` — the flat `ghcr.io/<owner>/telemulator`
  name is a tombstone from a deleted repository and cannot be written to. The
  nested `ghcr.io/<owner>/<repo>/emulator` name is deliberate.
- `Dockerfile` repeats the runtime dependencies by hand instead of installing
  the package. Adding a runtime dependency to `pyproject.toml` and not to the
  Dockerfile produces an image that dies at startup, and no test catches it.
- `network.py:dump()/load()` is the snapshot format the Admin API and
  `SqliteStore` both persist. Changing a field name breaks restore of anything
  already written.

## 8. Where things stand

- `main` on GitHub is `f86e3f4`, the merge of PR #1. Nothing else is open;
  there are no unmerged branches and no stashes of substance.
- The released version is `0.2.1`. The downstream consumer pins the package by
  that tag in its dev extras and the image by the same tag in its e2e compose
  overlay, so a version bump here is a two-line change over there.
- One known defect, deliberately parked:
  `docs/backlog/2026-08-15-network-messages-resolves-ambiguously.md` —
  `Network.messages(chat_id)` resolves a thread from a bare chat id, which is
  ambiguous once a person has a second chat, and `tests/test_network.py` pins
  the resulting `ValueError` as expected. The design half is already solved by
  `thread_for(viewer_id, peer_id)` / `chats_for(viewer_id)`, which is what
  `user_http.py` uses exclusively; what remains is deleting the ambiguous road
  and its callers. Roughly half an hour, no urgency.
- Coverage is thinnest where the browser and the process boundary live:
  `server.py` 35% (nothing starts a real uvicorn in tests except indirectly),
  `client.py` 60%, `admin_api.py` 71%. New work in those files needs its own
  tests or it eats the 0.1 pp of gate margin.
- Wave 4 planning starts from `docs/specs/2026-08-13-test-telegram-design.md`
  (Russian). Coverage of the Bot API grows only when a real bot hits a hole —
  an unknown method answers `404` and lands in the Admin journal, which is
  where the next thing to implement comes from.
