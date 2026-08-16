from __future__ import annotations

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from telemulator import create_app
from telemulator.network import Network
from telemulator.store import MemoryStore, SqliteStore

TOKEN = "111111111:AAFakeBotTokenForE2ETests0000000"


def test_sqlite_survives_new_network(tmp_path: Path) -> None:
  path = tmp_path / "net.sqlite"
  net = Network()
  net.create_user(id=5, first_name="Alive")
  SqliteStore(path).save(net.dump())
  restored = Network()
  restored.load(SqliteStore(path).load())
  assert restored.users[5]["first_name"] == "Alive"


def test_sqlite_load_empty_returns_none(tmp_path: Path) -> None:
  assert SqliteStore(tmp_path / "empty.sqlite").load() is None


def test_memory_store_roundtrip() -> None:
  store = MemoryStore()
  assert store.load() is None
  store.save({"users": [{"id": 1, "first_name": "M"}]})
  assert store.load()["users"][0]["first_name"] == "M"


def test_create_app_without_sqlite_path_has_no_store() -> None:
  app = create_app()
  assert app.state.store is None


async def test_create_app_reloads_sqlite_across_instances(tmp_path: Path) -> None:
  path = tmp_path / "net.sqlite"
  app = create_app(sqlite_path=str(path))
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 5, "first_name": "Alive"})
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post(f"/bot{TOKEN}/getMe")
  app2 = create_app(sqlite_path=str(path))
  assert app2.state.network.users[5]["first_name"] == "Alive"
  assert TOKEN in app2.state.network.bots


async def test_snapshot_restore_persists_to_sqlite(tmp_path: Path) -> None:
  path = tmp_path / "net.sqlite"
  app = create_app(sqlite_path=str(path))
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/users", json={"id": 7, "first_name": "X"})
    snap = (await client.post("/admin/snapshot")).json()
    await client.post("/admin/reset")
    await client.post("/admin/snapshot/restore", json=snap)
  app2 = create_app(sqlite_path=str(path))
  assert app2.state.network.users[7]["first_name"] == "X"


class _CountingStore:
  def __init__(self) -> None:
    self.saves = 0

  def load(self) -> None:
    return None

  def save(self, data: dict) -> None:
    self.saves += 1


async def test_polling_does_not_rewrite_the_snapshot() -> None:
  app = create_app()
  app.state.store = _CountingStore()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    await client.post("/admin/bots", json={"token": TOKEN})
    await client.post("/admin/users", json={"id": 1, "first_name": "A"})
    await client.post("/admin/dialogs", json={"user_id": 1, "bot_token": TOKEN})
    before = app.state.store.saves

    for _ in range(5):
      await client.post(f"/bot{TOKEN}/getUpdates", data={"timeout": "0"})
    await client.post("/admin/snapshot")
    assert app.state.store.saves == before

    await client.post(f"/bot{TOKEN}/sendMessage", data={"chat_id": "1", "text": "x"})
    assert app.state.store.saves == before + 1

