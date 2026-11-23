"""First launch (create master password) and unlock screens."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static

from botdesk.crypto import Secret, WrongPassword, decrypt_token, encrypt_token
from botdesk.ui.dialogs import ConfirmDialog
from botdesk.validation import MIN_PASSWORD_LENGTH

NO_RECOVERY_WARNING = (
    "Warning: a forgotten master password cannot be recovered. "
    "You would have to paste the bot token again and set a new password."
)


class SetupScreen(Screen):
    """First launch: create the master password."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="auth-box"):
            yield Static("Welcome to BotDesk", classes="heading")
            yield Static(
                "Create a master password. It encrypts your bot token on this computer "
                "and is asked every time BotDesk starts."
            )
            yield Static(NO_RECOVERY_WARNING, classes="warning")
            yield Input(password=True, placeholder=f"Master password (at least {MIN_PASSWORD_LENGTH} characters)", id="pw1")
            yield Input(password=True, placeholder="Repeat master password", id="pw2")
            yield Static("", id="error", classes="error", markup=False)
            yield Button("Create password", id="create", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#pw1", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "pw1":
            self.query_one("#pw2", Input).focus()
        else:
            await self._create()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        await self._create()

    async def _create(self) -> None:
        pw1 = self.query_one("#pw1", Input).value
        pw2 = self.query_one("#pw2", Input).value
        error = self.query_one("#error", Static)
        if len(pw1) < MIN_PASSWORD_LENGTH:
            error.update(f"The password must have at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if pw1 != pw2:
            error.update("The passwords do not match.")
            return
        error.update("Working...")
        vault = await asyncio.to_thread(encrypt_token, "", pw1)
        self.app.store.set("vault", vault)
        await self.app.unlocked(Secret(pw1), Secret(""))


class UnlockScreen(Screen):
    """Normal launch: unlock the token with the master password."""

    BINDINGS = [Binding("ctrl+r", "forgot", "Forgot password")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(classes="auth-box"):
            yield Static("Unlock BotDesk", classes="heading")
            yield Static("Enter your master password to unlock the bot token.")
            yield Input(password=True, placeholder="Master password", id="pw")
            yield Static("", id="error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Unlock", id="unlock", variant="primary")
                yield Button("Forgot password", id="forgot")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#pw", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._unlock()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "unlock":
            await self._unlock()
        else:
            self.action_forgot()

    async def _unlock(self) -> None:
        field = self.query_one("#pw", Input)
        error = self.query_one("#error", Static)
        password = field.value
        if not password:
            error.update("Enter the master password.")
            return
        error.update("Checking...")
        try:
            token = await asyncio.to_thread(decrypt_token, self.app.store.get("vault"), password)
        except WrongPassword:
            field.value = ""
            error.update("Wrong password. Try again.")
            return
        problem = await self.app.unlocked(Secret(password), Secret(token))
        if problem:
            field.value = ""
            error.update(problem)

    def action_forgot(self) -> None:
        def reset(confirmed: bool | None) -> None:
            if confirmed:
                self.app.reset_vault()
                self.app.switch_screen(SetupScreen())

        self.app.push_screen(
            ConfirmDialog(
                "A forgotten password cannot be recovered.\n\n"
                "Reset deletes the saved bot token. You will set a new master password "
                "and paste a token for the same bot in Settings. Commands, chats and files "
                "are kept. If this older profile has bot data but no recorded bot identity, "
                "resetting leaves it offline; use a new BOTDESK_HOME to reconnect.\n\n"
                "Reset now?",
                yes_label="Reset",
            ),
            reset,
        )
