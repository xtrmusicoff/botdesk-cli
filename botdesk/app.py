"""BotDesk Textual application."""

from __future__ import annotations

import asyncio
import logging

from textual.app import App

from botdesk.crypto import Secret, WrongPassword, decrypt_token, encrypt_token
from botdesk.engine import Engine
from botdesk.logsetup import register_secret, setup_logging
from botdesk.paths import data_dir
from botdesk.store import IncompatibleDatabase, Store
from botdesk.validation import looks_like_token

log = logging.getLogger("botdesk")


class BotDeskApp(App):
    CSS_PATH = "ui/botdesk.tcss"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, store: Store, data_path) -> None:
        super().__init__()
        self.store = store
        self.data_path = data_path
        self.default_files_folder = data_path / "files"
        self._password = Secret("")
        self._token = Secret("")
        self.engine = Engine(
            store,
            on_change=self._engine_changed,
            on_alert=self._engine_alert,
            on_status=self._engine_status,
            on_error=self._engine_error,
        )

    # -------------------------------------------------------------- startup
    def on_mount(self) -> None:
        from botdesk.ui.auth import SetupScreen, UnlockScreen

        self.update_title()
        self.push_screen(UnlockScreen() if self.store.has_setting("vault") else SetupScreen())

    def _bind_identity(self, token: str, *, from_vault: bool = False) -> str | None:
        """Check/bind the profile while the caller holds a store transaction."""
        bound = self.store.get("profile_bot_id")
        if not bound and not from_vault and self.store.has_bot_state():
            # A legacy password reset can leave chats and pending deliveries
            # without any credential that establishes which bot owned them.
            bound = "unknown"
            self.store.set("profile_bot_id", bound)
        if not token:
            return None  # The operator may still inspect local records offline.
        if not token.isascii() or not looks_like_token(token):
            return "That does not look like a BotFather token (digits, colon, then letters)."
        bot_id = token.split(":", 1)[0]
        if bound == "unknown":
            return ("The original bot identity is unknown. Keep this profile offline and "
                    "set BOTDESK_HOME to a new folder for your bot.")
        if bound and bound != bot_id:
            return ("This profile belongs to a different bot. Use a token for the original "
                    "bot or set BOTDESK_HOME to a new folder.")
        if not bound:
            self.store.set("profile_bot_id", bot_id)
        return None

    async def unlocked(self, password: Secret, token: Secret) -> str | None:
        from botdesk.ui.menu import MainMenuScreen

        with self.store.transaction():
            problem = self._bind_identity(token.reveal(), from_vault=bool(token))
        if problem:
            return problem
        self._password = password
        self._token = token
        register_secret(token.reveal())
        self.switch_screen(MainMenuScreen())
        await self.engine.start(token.reveal() or None)
        self.refresh_views()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # --------------------------------------------------------- token, password
    async def save_token(self, token: str) -> str | None:
        """Encrypt and save a new token. Returns an error message or None."""
        token = token.strip()
        if not token or not token.isascii() or not looks_like_token(token):
            return "That does not look like a BotFather token (digits, colon, then letters)."
        vault = await asyncio.to_thread(encrypt_token, token, self._password.reveal())
        with self.store.transaction():
            problem = self._bind_identity(token)
            if problem:
                return problem
            self.store.set("vault", vault)
        self._token = Secret(token)
        register_secret(token)
        log.info("Bot token saved")
        await self.engine.start(token)
        self.refresh_views()
        return None

    def reset_vault(self) -> None:
        """Forget the credential, but never release ownership of bot data."""
        with self.store.transaction():
            self._bind_identity("")
            self.store.delete_setting("vault")

    async def change_password(self, current: str, new: str) -> str | None:
        try:
            token = await asyncio.to_thread(decrypt_token, self.store.get("vault"), current)
        except WrongPassword:
            return "The current password is wrong."
        vault = await asyncio.to_thread(encrypt_token, token, new)
        self.store.set("vault", vault)
        self._password = Secret(new)
        log.info("Master password changed")
        return None

    # ----------------------------------------------------- engine callbacks
    def _engine_changed(self) -> None:
        self.refresh_views()

    def _engine_alert(self, message: str) -> None:
        self.bell()
        self.notify(message, title="New item", markup=False)

    def _engine_status(self, status: str) -> None:
        self.refresh_views()

    def _engine_error(self, message: str) -> None:
        self.notify(message, severity="error", timeout=10, markup=False)

    def refresh_views(self) -> None:
        """Refresh every screen on the stack that shows live data."""
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_data", None)
            if refresh is not None and screen.is_mounted:
                try:
                    refresh()
                except Exception:
                    log.exception("Failed to refresh %s", type(screen).__name__)

    def update_title(self) -> None:
        name = self.store.get("bot_name").strip() or "Unnamed bot"
        self.title = f"BotDesk - {name}"
        self.sub_title = ""
        # Set the terminal window title (OSC 0). Control characters are removed
        # so a bot name cannot inject terminal escape sequences.
        safe = "".join(ch for ch in self.title if ch.isprintable())
        driver = getattr(self, "_driver", None)
        if driver is not None:
            try:
                driver.write(f"\x1b]0;{safe}\x07")
            except Exception:
                pass

    # ----------------------------------------------------------------- quit
    async def action_quit(self) -> None:
        await self.engine.stop()
        self.exit()

    async def on_unmount(self) -> None:
        await self.engine.stop()

    def on_ready(self) -> None:
        self.update_title()


def main() -> None:
    path = data_dir()
    setup_logging(path / "botdesk.log")
    log.info("BotDesk starting; data folder %s", path)
    try:
        store = Store(path / "botdesk.db")
    except IncompatibleDatabase as exc:
        log.error("%s", exc)
        raise SystemExit(f"BotDesk cannot start: {exc}") from None
    try:
        BotDeskApp(store, path).run()
    finally:
        store.close()
        log.info("BotDesk stopped")
