from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from telemulator import create_app


async def test_app_serves_health() -> None:
  app = create_app()
  transport = ASGITransport(app=app)
  async with AsyncClient(transport=transport, base_url="http://tg") as client:
    response = await client.get("/health")
  assert response.status_code == 200
  assert response.json() == {"status": "ok"}


def test_public_surface_is_importable() -> None:
  import telemulator

  for name in telemulator.__all__:
    assert hasattr(telemulator, name), name
