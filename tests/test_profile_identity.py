"""Profile identity and recovery tests; no real credentials or network calls."""
from unittest.mock import AsyncMock, Mock

import pytest

from botdesk.app import BotDeskApp
from botdesk.crypto import Secret, decrypt_token, encrypt_token
from botdesk.store import Store

FIRST = "123456:" + "a" * 32
ROTATED = "123456:" + "b" * 32
OTHER = "987654:" + "c" * 32
PASSWORD = "example-password"


@pytest.fixture
def profile(tmp_path):
    store = Store(tmp_path / "profile.db")
    app = BotDeskApp(store, tmp_path)
    app.engine.start = AsyncMock()
    app.switch_screen = Mock()
    app.refresh_views = Mock()
    try:
        yield app, store
    finally:
        store.close()


async def test_rotation_preserves_state_and_different_bot_is_rejected(profile):
    app, store = profile
    await app.unlocked(Secret(PASSWORD), Secret(""))
    assert await app.save_token(FIRST) is None
    store.set("update_offset", "10")
    store.enqueue(42, "original bot's reply", 0)
    assert await app.save_token(ROTATED) is None
    assert store.get("profile_bot_id") == "123456"
    assert store.get("update_offset") == "10"
    vault = store.get("vault")
    assert decrypt_token(vault, PASSWORD) == ROTATED
    app.engine.start.reset_mock()
    assert "different bot" in await app.save_token(OTHER)
    assert store.get("vault") == vault
    assert [item.text for item in store.pending_outbox()] == ["original bot's reply"]
    app.engine.start.assert_not_awaited()


async def test_unlocked_legacy_profile_binds_before_polling_and_survives_reset(profile):
    app, store = profile
    store.set("vault", encrypt_token(FIRST, PASSWORD))
    store.set("update_offset", "22")
    async def check_binding(token):
        assert store.get("profile_bot_id") == "123456"
    app.engine.start.side_effect = check_binding
    assert await app.unlocked(Secret(PASSWORD), Secret(FIRST)) is None
    app.reset_vault()
    assert not store.has_setting("vault")
    assert store.get("update_offset") == "22"
    await app.unlocked(Secret("new-password"), Secret(""))
    assert "different bot" in await app.save_token(OTHER)
    assert await app.save_token(ROTATED) is None
    assert decrypt_token(store.get("vault"), "new-password") == ROTATED


@pytest.mark.parametrize("reset", [True, False])
async def test_legacy_unowned_state_is_not_adopted(profile, reset):
    app, store = profile
    if reset:
        store.set("vault", encrypt_token(FIRST, PASSWORD))
    store.enqueue(42, "legacy reply", 0)
    if reset:
        app.reset_vault()  # cannot decrypt old token without the password
    await app.unlocked(Secret(PASSWORD), Secret(""))
    app.engine.start.reset_mock()
    assert "identity is unknown" in await app.save_token(OTHER)
    assert len(store.pending_outbox()) == 1
    app.engine.start.assert_not_awaited()


async def test_mismatched_saved_token_does_not_start_engine(profile):
    app, store = profile
    store.set("profile_bot_id", "123456")
    assert "different bot" in await app.unlocked(Secret(PASSWORD), Secret(OTHER))
    app.engine.start.assert_not_awaited()
    assert not app.has_token
