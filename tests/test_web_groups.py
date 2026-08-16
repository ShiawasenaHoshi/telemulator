from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from telemulator import create_app


async def test_web_client_has_group_channel_members_and_admin_checkbox() -> None:
  app = create_app()
  async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tg") as client:
    html = (await client.get("/")).text
    js = (await client.get("/app.js")).text
    css = (await client.get("/style.css")).text
    assert "Telegram" not in html
    assert "telemulator" in html
    assert "Группа" in html
    assert "Канал" in html
    assert 'id="new-group"' in html
    assert 'id="new-channel"' in html
    assert 'id="members"' in html
    assert 'id="add-as-admin"' in html
    assert "/user/chats" in js
    assert 'type: "supergroup"' in js
    assert 'type: "channel"' in js
    assert 'body.status = "administrator"' in js
    assert "composer.hidden" in js
    assert "sender_chat" in js
    assert "new_chat_members" in js
    assert "left_chat_member" in js
    assert "chat.title" in js
    assert "#members" in css
    assert "form-error" in js
    assert "form-error" in css
    assert "withForm" in js
    assert "body.detail" in js
