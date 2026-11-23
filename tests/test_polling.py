"""Polling, outbox and reconnect behavior against a fake aiogram Bot."""

import asyncio
from types import SimpleNamespace

from botdesk import store as st
from botdesk.engine import CONNECTED, RECONNECTING, Engine
from botdesk.store import Store


def tg_message(chat_id, text=None, document=None, caption=None):
    user = SimpleNamespace(id=chat_id, first_name="Bob", last_name=None, username="bob")
    chat = SimpleNamespace(id=chat_id, title=None)
    return SimpleNamespace(
        chat=chat, from_user=user, text=text, caption=caption, document=document, photo=None,
        video=None, audio=None, animation=None, voice=None, video_note=None,
    )


class FakeBot:
    def __init__(self, batches, fail_first=0):
        self.batches = list(batches)
        self.fail_first = fail_first
        self.sent = []
        self.menus = []
        self.offsets = []

    async def get_me(self):
        return SimpleNamespace(username="test_bot")

    async def delete_webhook(self, drop_pending_updates=False):
        return True

    async def get_updates(self, offset=None, timeout=None, allowed_updates=None, request_timeout=None):
        self.offsets.append(offset)
        if self.fail_first:
            self.fail_first -= 1
            raise OSError("network down")
        if self.batches:
            return self.batches.pop(0)
        await asyncio.sleep(0.05)
        return []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    async def set_my_commands(self, commands):
        self.menus.append([c.command for c in commands])


async def run_engine(engine, bot, until, timeout=5.0):
    engine.bot = bot
    engine._tasks = [
        asyncio.create_task(engine._poll_loop()),
        asyncio.create_task(engine._outbox_loop()),
    ]
    try:
        async with asyncio.timeout(timeout):
            while not until():
                await asyncio.sleep(0.02)
    finally:
        engine.bot = None  # keep stop() from closing a real session
        await engine.stop()


async def test_poll_routes_updates_and_sends(tmp_path):
    store = Store(tmp_path / "p.db")
    cid = store.add_command("start")
    store.add_step(cid, st.SEND_MESSAGE, "Welcome!")
    store.add_step(cid, st.WAIT_FILE, "")
    doc = SimpleNamespace(file_id="F1", file_unique_id="U1", file_name="cv.docx", file_size=10)
    updates = [
        [SimpleNamespace(update_id=10, message=tg_message(1, text="/start@test_bot"))],
        [SimpleNamespace(update_id=11, message=tg_message(1, document=doc, caption="my cv"))],
    ]
    statuses = []
    engine = Engine(store, on_status=statuses.append)
    bot = FakeBot(updates)
    await run_engine(engine, bot, lambda: bot.sent and store.list_files())
    assert bot.sent == [(1, "Welcome!")]
    [item] = store.list_files()
    assert (item.file_name, item.caption, item.user_label) == ("cv.docx", "my cv", "Bob (@bob)")
    assert store.get("update_offset") == "12"
    assert CONNECTED in statuses
    store.close()


async def test_reconnects_after_network_error(tmp_path, monkeypatch):
    import botdesk.engine as engine_module

    store = Store(tmp_path / "r.db")
    statuses = []
    engine = Engine(store, on_status=statuses.append)
    bot = FakeBot([], fail_first=2)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(engine_module.asyncio, "sleep", lambda s: real_sleep(min(s, 0.01)))
    await run_engine(engine, bot, lambda: engine.status == CONNECTED)
    assert statuses[:2] == [RECONNECTING, CONNECTED]
    store.close()


async def test_persistently_failing_update_is_skipped_after_limit(tmp_path, monkeypatch):
    import botdesk.engine as engine_module

    store = Store(tmp_path / "k.db")
    cid = store.add_command("start")
    store.add_step(cid, st.SEND_MESSAGE, "Hi")
    engine = Engine(store)
    real_handle = engine.handle

    def flaky(msg):
        if msg.text == "boom":
            raise RuntimeError("bad update")
        real_handle(msg)

    monkeypatch.setattr(engine, "handle", flaky)
    monkeypatch.setattr(engine_module, "MAX_UPDATE_ATTEMPTS", 3)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(engine_module.asyncio, "sleep", lambda s: real_sleep(min(s, 0.01)))
    # Telegram redelivers update 10 until the limit of 3 attempts is reached, then update 11.
    bot = FakeBot([
        [SimpleNamespace(update_id=10, message=tg_message(1, text="boom"))],
        [SimpleNamespace(update_id=10, message=tg_message(1, text="boom"))],
        [SimpleNamespace(update_id=10, message=tg_message(1, text="boom"))],
        [SimpleNamespace(update_id=11, message=tg_message(1, text="/start"))],
    ])
    await run_engine(engine, bot, lambda: bot.sent)
    assert bot.sent == [(1, "Hi")]
    assert store.get("update_offset") == "12"
    store.close()


async def test_offline_messages_are_sent_after_connect(tmp_path):
    store = Store(tmp_path / "o.db")
    engine = Engine(store)
    engine.send(5, "queued while offline")
    assert len(store.pending_outbox()) == 1
    bot = FakeBot([])
    await run_engine(engine, bot, lambda: bot.sent)
    assert bot.sent == [(5, "queued while offline")]
    assert store.pending_outbox() == []
    store.close()


async def test_menu_sync_only_when_enabled(tmp_path):
    store = Store(tmp_path / "m.db")
    store.add_command("start")
    store.add_command("help")
    engine = Engine(store)
    engine.request_menu_sync()  # toggle off: nothing happens
    assert store.get("menu_dirty") == ""
    store.set("sync_commands", "1")
    engine.request_menu_sync()
    bot = FakeBot([])
    await run_engine(engine, bot, lambda: bot.menus)
    assert bot.menus == [["help", "start"]]
    store.close()
