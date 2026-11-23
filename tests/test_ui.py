"""UI smoke tests with Textual's pilot (no network: no token is set)."""

from botdesk import store as st
from botdesk.app import BotDeskApp
from botdesk.crypto import decrypt_token
from botdesk.engine import Incoming
from botdesk.store import Store


def make_app(tmp_path):
    store = Store(tmp_path / "ui.db")
    return BotDeskApp(store, tmp_path), store


async def wait_for(pilot, predicate, tries=100):
    for _ in range(tries):
        if predicate():
            return
        await pilot.pause(0.05)
    raise AssertionError("condition not reached")


async def setup_password(pilot, app):
    await pilot.press(*"secret123", "enter", *"secret123", "enter")
    await wait_for(pilot, lambda: type(app.screen).__name__ == "MainMenuScreen")


async def test_first_launch_creates_vault_and_title(tmp_path):
    app, store = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        assert type(app.screen).__name__ == "SetupScreen"
        await setup_password(pilot, app)
        assert app.title == "BotDesk - Unnamed bot"
        assert decrypt_token(store.get("vault"), "secret123") == ""
        assert app.engine.status == "offline"
        await app.action_quit()


async def test_unlock_wrong_then_right_password(tmp_path):
    from botdesk.crypto import encrypt_token

    app, store = make_app(tmp_path)
    store.set("vault", encrypt_token("", "goodpass1"))
    async with app.run_test(size=(110, 40)) as pilot:
        assert type(app.screen).__name__ == "UnlockScreen"
        await pilot.press(*"badpass", "enter")
        await wait_for(pilot, lambda: "Wrong password" in str(app.screen.query_one("#error").render()))
        await pilot.press(*"goodpass1", "enter")
        await wait_for(pilot, lambda: type(app.screen).__name__ == "MainMenuScreen")
        await app.action_quit()


async def test_commands_ui_add_command_and_steps(tmp_path):
    app, store = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await setup_password(pilot, app)
        await pilot.press("c")
        await pilot.press("a", *"Bad Name", "enter")
        assert store.list_commands() == []  # rejected, dialog still open
        await pilot.press("escape")
        await pilot.press("a", *"support", "enter")
        await wait_for(pilot, lambda: type(app.screen).__name__ == "CommandStepsScreen")
        # step 1: send message
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#text").text = "Hello there"
        await pilot.press("ctrl+s")
        # step 2: wait for file
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#action").value = st.WAIT_FILE
        await pilot.press("ctrl+s")
        await pilot.pause()
        cid = store.get_command_by_name("support").id
        assert [(s.action, s.text) for s in store.list_steps(cid)] == [
            (st.SEND_MESSAGE, "Hello there"), (st.WAIT_FILE, ""),
        ]
        await pilot.press("shift+up")  # move "wait for file" up
        await pilot.pause()
        assert [s.action for s in store.list_steps(cid)] == [st.WAIT_FILE, st.SEND_MESSAGE]
        await app.action_quit()


async def test_settings_invalid_window_keeps_previous(tmp_path):
    app, store = make_app(tmp_path)
    async with app.run_test(size=(110, 60)) as pilot:
        await setup_password(pilot, app)
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        for bad in ["abc", "0", "-1", "29", "90000"]:
            screen.query_one("#live_window").value = bad
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert "whole number" in str(screen.query_one("#error").render())
            assert store.get("live_window") == "600"
        screen.query_one("#live_window").value = "120"
        screen.query_one("#bot_name").value = "Support Bot"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert store.get("live_window") == "120"
        assert app.title == "BotDesk - Support Bot"
        await app.action_quit()


async def test_main_menu_shows_version_profile_and_skipped_count(tmp_path):
    from botdesk import __version__

    app, store = make_app(tmp_path)
    store.set("skipped_updates", "2")
    async with app.run_test(size=(110, 40)) as pilot:
        await setup_password(pilot, app)
        menu = app.screen.query_one("#menu")
        assert "Skipped updates: 2" in str(menu.get_option("live").prompt)
        profile = str(app.screen.query_one("#profile").render())
        assert __version__ in profile and str(tmp_path) in profile
        await app.action_quit()


async def test_opening_live_unread_clears_skipped_count(tmp_path):
    app, store = make_app(tmp_path)
    store.set("skipped_updates", "2")
    async with app.run_test(size=(110, 40)) as pilot:
        await setup_password(pilot, app)
        await pilot.press("l")
        await wait_for(pilot, lambda: type(app.screen).__name__ == "LiveScreen")
        assert store.get("skipped_updates") == ""
        await pilot.press("escape")
        await wait_for(pilot, lambda: type(app.screen).__name__ == "MainMenuScreen")
        assert "Skipped" not in str(app.screen.query_one("#menu").get_option("live").prompt)
        await app.action_quit()


async def test_live_chat_reply_from_ui(tmp_path):
    app, store = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await setup_password(pilot, app)
        cid = store.add_command("talk")
        store.add_step(cid, st.LIVE_CHAT, "")
        app.engine.handle(Incoming(chat_id=42, user_label="Alice", text="/talk"))
        app.engine.handle(Incoming(chat_id=42, user_label="Alice", text="Hi owner"))
        await pilot.pause()
        menu = app.screen.query_one("#menu")
        assert "Live / Unread (1)" in str(menu.get_option("live").prompt)
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("enter")  # open the chat
        await wait_for(pilot, lambda: type(app.screen).__name__ == "ChatScreen")
        await pilot.press(*"Hello Alice", "enter")
        await pilot.pause()
        chat = store.list_chats()[0]
        assert chat.state == st.ACCEPTED and not chat.unread
        assert [m.text for m in store.chat_messages(chat.id)] == ["Hi owner", "Hello Alice"]
        await pilot.press("ctrl+l", "y")
        await pilot.pause()
        assert store.get_chat(chat.id).state == st.CLOSED
        await app.action_quit()
