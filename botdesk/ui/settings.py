"""Settings screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static, Switch

from botdesk.ui.dialogs import ChangePasswordDialog
from botdesk.validation import parse_window

MESSAGE_FIELDS = [
    ("msg_connecting", "\"Connecting you with the bot's owner\" (sent when a live chat is requested)"),
    ("msg_expired", "\"Request expired\" (sent when a live chat request expires)"),
    ("msg_closed", "\"Chat closed\" (sent when you close a live chat)"),
    ("msg_unknown", "\"Unknown command\" (reply to a /command that does not exist)"),
]


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        store = self.app.store
        yield Header()
        with VerticalScroll(id="settings"):
            yield Label("Bot name (local label for the window title; never sent to Telegram)")
            yield Input(store.get("bot_name"), placeholder="Unnamed bot", id="bot_name", max_length=64)

            yield Label("Bot token (from BotFather)")
            yield Static("", id="token_state", classes="hint")
            with Horizontal(classes="row"):
                yield Input(password=True, placeholder="Paste a new token here", id="token")
                yield Button("Save token", id="save_token", variant="primary")

            yield Label("Master password")
            yield Button("Change master password", id="change_password")

            for key, label in MESSAGE_FIELDS:
                yield Label(label)
                yield Input(store.get(key), id=key, max_length=4096)

            yield Label("Live chat window (seconds, 30 to 86400; default 600)")
            yield Input(store.get("live_window"), id="live_window", max_length=5)

            yield Label("Files folder (where saved files go)")
            yield Input(store.get("files_folder"), placeholder=str(self.app.default_files_folder), id="files_folder")

            yield Label("Sync Telegram command menu (updates the bot's / menu when commands change)")
            yield Switch(store.get("sync_commands") == "1", id="sync_commands")

            yield Static("", id="error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Save (ctrl+s)", id="save", variant="success")
                yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._update_token_state()
        self.query_one("#bot_name", Input).focus()

    def _update_token_state(self) -> None:
        text = "A token is saved (hidden)." if self.app.has_token else "No token saved yet."
        self.query_one("#token_state", Static).update(text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "save":
            self.action_save()
        elif button == "back":
            self.app.pop_screen()
        elif button == "save_token":
            await self._save_token()
        elif button == "change_password":
            def changed(ok: bool | None) -> None:
                if ok:
                    self.notify("Master password changed.")

            self.app.push_screen(ChangePasswordDialog(), changed)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "token":
            await self._save_token()
        else:
            self.action_save()

    async def _save_token(self) -> None:
        field = self.query_one("#token", Input)
        token = field.value.strip()
        error = self.query_one("#error", Static)
        problem = await self.app.save_token(token)
        field.value = ""
        if problem:
            error.update(problem)
            return
        error.update("")
        self._update_token_state()
        self.notify("Token saved and encrypted. Connecting...")

    def action_save(self) -> None:
        store = self.app.store
        error = self.query_one("#error", Static)
        window_field = self.query_one("#live_window", Input)
        try:
            window = parse_window(window_field.value)
        except ValueError as exc:
            error.update(str(exc))
            window_field.focus()
            return
        for key, _label in MESSAGE_FIELDS:
            if not self.query_one(f"#{key}", Input).value.strip():
                error.update("Messages cannot be empty.")
                self.query_one(f"#{key}", Input).focus()
                return
        folder = self.query_one("#files_folder", Input).value.strip()
        if folder:
            path = Path(folder).expanduser()
            if path.exists() and not path.is_dir():
                error.update("The files folder path points to a file, not a folder.")
                return

        store.set("bot_name", self.query_one("#bot_name", Input).value.strip())
        for key, _label in MESSAGE_FIELDS:
            store.set(key, self.query_one(f"#{key}", Input).value)
        store.set("live_window", str(window))
        window_field.value = str(window)
        store.set("files_folder", folder)
        sync_before = store.get("sync_commands") == "1"
        sync_now = self.query_one("#sync_commands", Switch).value
        store.set("sync_commands", "1" if sync_now else "0")
        if sync_now and not sync_before:
            self.app.engine.request_menu_sync()
        error.update("")
        self.app.update_title()
        self.notify("Settings saved.")
