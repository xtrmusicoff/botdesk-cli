"""Bot engine: Telegram polling, message routing, command steps, live chat expiry.

Routing logic works on plain `Incoming` objects and only writes to the store
(outgoing text goes to the outbox table). That keeps it testable without
Telegram and means no message is lost while the bot is offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from botdesk import store as st
from botdesk.store import Store
from botdesk.validation import (
    TELEGRAM_TEXT_LIMIT,
    parse_buttons,
    parse_delay,
    reserve_unique_file,
    sanitize_filename,
)

log = logging.getLogger("botdesk.engine")

CONNECTED = "connected"
RECONNECTING = "reconnecting"
OFFLINE = "offline"

TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024
POLL_TIMEOUT = 25
MAX_BACKOFF = 60
# A failing update is retried this many times in total, then skipped and logged.
MAX_UPDATE_ATTEMPTS = 5


@dataclass(frozen=True)
class IncomingFile:
    file_id: str
    file_name: str
    file_size: int | None


@dataclass(frozen=True)
class Incoming:
    chat_id: int
    user_label: str
    text: str | None = None
    caption: str | None = None
    file: IncomingFile | None = None


def _markup(keyboard: str | None):
    """Turn a stored keyboard (JSON list of labels) into a Telegram reply keyboard, or None.

    Each button is its own row. Labels are sent back as plain text when pressed.
    """
    if not keyboard:
        return None
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    labels = json.loads(keyboard)
    rows = [[KeyboardButton(text=label)] for label in labels]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Split text into Telegram-sized chunks, preferring line breaks."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def parse_command(text: str, bot_username: str | None) -> str | None:
    """Return the command name for '/name', '/name@bot args', or None if addressed to another bot."""
    head = text[1:].split(maxsplit=1)[0] if len(text) > 1 and not text[1].isspace() else ""
    name, _, mention = head.partition("@")
    if mention and bot_username and mention.lower() != bot_username.lower():
        return None
    return name.lower()


def user_label_of(user, chat) -> str:
    """Readable label for the person writing to the bot."""
    if user is not None:
        name = " ".join(p for p in (user.first_name, user.last_name) if p) or str(user.id)
        return f"{name} (@{user.username})" if user.username else name
    return chat.title or str(chat.id)


def incoming_from_message(message) -> Incoming:
    """Convert an aiogram Message into an Incoming."""
    file: IncomingFile | None = None
    if message.document:
        d = message.document
        file = IncomingFile(d.file_id, d.file_name or f"document_{d.file_unique_id}", d.file_size)
    elif message.photo:
        p = message.photo[-1]
        file = IncomingFile(p.file_id, f"photo_{p.file_unique_id}.jpg", p.file_size)
    else:
        for attr, ext in (("video", ".mp4"), ("audio", ".mp3"), ("animation", ".mp4"),
                          ("voice", ".ogg"), ("video_note", ".mp4")):
            media = getattr(message, attr, None)
            if media is not None:
                name = getattr(media, "file_name", None) or f"{attr}_{media.file_unique_id}{ext}"
                file = IncomingFile(media.file_id, name, media.file_size)
                break
    return Incoming(
        chat_id=message.chat.id,
        user_label=user_label_of(message.from_user, message.chat),
        text=message.text,
        caption=message.caption,
        file=file,
    )


class Engine:
    def __init__(
        self,
        store: Store,
        *,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[], None] | None = None,
        on_alert: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self._on_change = on_change or (lambda: None)
        self._on_alert = on_alert or (lambda _msg: None)
        self._on_status = on_status or (lambda _s: None)
        self._on_error = on_error or (lambda _msg: None)
        self.status = OFFLINE
        self.bot = None
        self.bot_username: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._wake = asyncio.Event()
        self._failed_updates: dict[int, int] = {}

    # ------------------------------------------------------------------ status
    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            log.info("Connection state: %s", status)
            self._on_status(status)

    # --------------------------------------------------------------- routing
    def handle(self, msg: Incoming) -> None:
        """Route one incoming message. See SPEC 6.2."""
        now = self.clock()
        text = msg.text
        if text is not None and text.startswith("/"):
            name = parse_command(text, self.bot_username)
            if name is None:
                return
            command = self.store.get_command_by_name(name) if name else None
            if command is None:
                self.send(msg.chat_id, self.store.get("msg_unknown"))
            else:
                # A pending "wait for file" stays active unless this command
                # reaches its own "wait for file" step (which replaces it).
                self._run_steps(msg, command.id, start_after=None)
            return

        if msg.file is not None:
            flow = self.store.get_flow(msg.chat_id)
            if flow is None:
                log.info("Ignored a file from chat %s: no 'wait for file' step is active", msg.chat_id)
                return
            command_id, step_id = flow
            self.store.clear_flow(msg.chat_id)
            item = self.store.add_file(
                msg.chat_id, msg.user_label, msg.file.file_id, msg.file.file_name,
                msg.caption or "", msg.file.file_size, now,
            )
            log.info("Received file #%s from chat %s", item.id, msg.chat_id)
            self._on_alert(f"New file from {msg.user_label}: {msg.file.file_name}")
            self._on_change()
            self._run_steps(msg, command_id, start_after=step_id)
            return

        if text is not None:
            chat = self.store.open_chat_for(msg.chat_id)
            if chat is not None:
                self.store.add_chat_message(chat.id, "user", text, now)
                self.store.set_chat_unread(chat.id, True)
                self._on_change()
                return
            flow = self.store.get_flow_full(msg.chat_id)
            if flow is not None and flow["kind"] == st.ASK_BUTTONS:
                step = next((s for s in self.store.list_steps(flow["command_id"]) if s.id == flow["step_id"]), None)
                _question, options = parse_buttons(step.text if step is not None else "")
                if text in options:
                    self.handle_button(msg.chat_id, msg.user_label, text)
                    return
                # Text that is not one of the buttons is ignored; the buttons stay open.
                return
            if flow is not None and flow["kind"] == st.ASK_QUESTION:
                self._answer_flow(msg, flow, text)
            # Other plain text with no open chat or question is not stored (SPEC 6.2).
            return

    def _answer_flow(self, msg: Incoming, flow, answer: str) -> None:
        """The user answered an 'ask a question' step: store the answer and continue."""
        step = next((s for s in self.store.list_steps(flow["command_id"]) if s.id == flow["step_id"]), None)
        question = step.text if step is not None else ""
        self.store.clear_flow(msg.chat_id)
        self.store.add_answer(msg.chat_id, msg.user_label, question, answer, self.clock())
        log.info("Answer stored from chat %s", msg.chat_id)
        self._on_alert(f"New answer from {msg.user_label}")
        self._on_change()
        self._run_steps(msg, flow["command_id"], start_after=flow["step_id"])

    def handle_button(self, chat_id: int, user_label: str, label: str) -> None:
        """The user pressed a button from an 'ask with buttons' step."""
        flow = self.store.get_flow_full(chat_id)
        if flow is None or flow["kind"] != st.ASK_BUTTONS:
            return  # stale button: the step already moved on
        step = next((s for s in self.store.list_steps(flow["command_id"]) if s.id == flow["step_id"]), None)
        question, _options = parse_buttons(step.text if step is not None else "")
        msg = Incoming(chat_id=chat_id, user_label=user_label)
        self.store.clear_flow(chat_id)
        self.store.add_answer(chat_id, user_label, question, label, self.clock())
        log.info("Button answer stored from chat %s", chat_id)
        self._on_alert(f"New answer from {user_label}")
        self._on_change()
        self._run_steps(msg, flow["command_id"], start_after=flow["step_id"])

    def resume_due_delays(self) -> int:
        """Resume flows whose delay has passed. Returns how many resumed."""
        now = self.clock()
        due = self.store.due_delay_flows(now)
        for flow in due:
            self.store.clear_flow(flow["chat_id"])
            msg = Incoming(chat_id=flow["chat_id"], user_label="")
            self._run_steps(msg, flow["command_id"], start_after=flow["step_id"])
        if due:
            self._on_change()
        return len(due)

    def _run_steps(self, msg: Incoming, command_id: int, start_after: int | None) -> None:
        steps = self.store.list_steps(command_id)
        if start_after is not None:
            ids = [s.id for s in steps]
            if start_after not in ids:
                return
            steps = steps[ids.index(start_after) + 1:]
        for step in steps:
            if step.action == st.SEND_MESSAGE:
                if step.text.strip():
                    self.send(msg.chat_id, step.text)
            elif step.action == st.LIVE_CHAT:
                self._request_live_chat(msg)
            elif step.action == st.WAIT_FILE:
                self.store.set_flow(msg.chat_id, command_id, step.id, kind=st.WAIT_FILE)
                return
            elif step.action == st.ASK_QUESTION:
                if step.text.strip():
                    self.send(msg.chat_id, step.text)
                self.store.set_flow(msg.chat_id, command_id, step.id, kind=st.ASK_QUESTION)
                return
            elif step.action == st.ASK_BUTTONS:
                question, options = parse_buttons(step.text)
                if not options:
                    log.warning("Step #%s has no button options; skipped", step.id)
                    continue
                self.send(msg.chat_id, question, keyboard=options)
                self.store.set_flow(msg.chat_id, command_id, step.id, kind=st.ASK_BUTTONS)
                return
            elif step.action == st.WAIT_DELAY:
                seconds = parse_delay(step.text)
                if seconds is None:
                    log.warning("Step #%s has an invalid delay; skipped", step.id)
                    continue
                due = self.clock() + seconds
                self.store.set_flow(msg.chat_id, command_id, step.id, kind=st.WAIT_DELAY, due_at=due)
                self._wake.set()
                return

    def _request_live_chat(self, msg: Incoming) -> None:
        now = self.clock()
        self.send(msg.chat_id, self.store.get("msg_connecting"))
        if self.store.open_chat_for(msg.chat_id) is not None:
            return  # already has an open sub-chat; do not open a second one
        window = int(self.store.get("live_window"))
        chat = self.store.create_chat(msg.chat_id, msg.user_label, now, now + window)
        log.info("Live chat #%s requested by chat %s", chat.id, msg.chat_id)
        self._on_alert(f"Live chat request from {msg.user_label}")
        self._on_change()

    # --------------------------------------------------------- owner actions
    def owner_reply(self, ref: int, text: str) -> None:
        chat = self.store.get_chat(ref)
        if chat is None or not chat.is_open:
            raise ValueError("This chat is no longer open.")
        now = self.clock()
        self.store.add_chat_message(ref, "owner", text, now)
        if chat.state == st.WAITING:
            self.store.set_chat_state(ref, st.ACCEPTED, now)
        self.send(chat.chat_id, text)
        self._on_change()

    def close_chat(self, ref: int) -> None:
        chat = self.store.get_chat(ref)
        if chat is None or not chat.is_open:
            raise ValueError("This chat is no longer open.")
        self.store.set_chat_state(ref, st.CLOSED, self.clock())
        self.send(chat.chat_id, self.store.get("msg_closed"))
        log.info("Live chat #%s closed by owner", ref)
        self._on_change()

    def expire_due(self) -> int:
        """Expire waiting requests whose window has passed. Returns how many."""
        now = self.clock()
        due = self.store.due_waiting_chats(now)
        for chat in due:
            self.store.set_chat_state(chat.id, st.EXPIRED, now)
            self.send(chat.chat_id, self.store.get("msg_expired"))
            log.info("Live chat #%s expired", chat.id)
        if due:
            self._on_change()
        return len(due)

    def send(self, chat_id: int, text: str, keyboard: list[str] | None = None) -> None:
        """Queue a message. A keyboard, if given, is attached to the last chunk."""
        now = self.clock()
        chunks = split_text(text)
        for index, chunk in enumerate(chunks):
            markup = None
            if keyboard and index == len(chunks) - 1:
                markup = json.dumps(keyboard)
            self.store.enqueue(chat_id, chunk, now, keyboard=markup)
        self._wake.set()

    def request_menu_sync(self) -> None:
        """Mark the Telegram command menu as needing an update (if sync is on)."""
        if self.store.get("sync_commands") == "1":
            self.store.set("menu_dirty", "1")
            self._wake.set()

    def files_folder(self, default: Path) -> Path:
        value = self.store.get("files_folder").strip()
        return Path(value).expanduser() if value else default

    async def save_file(self, ref: int, folder: Path) -> Path:
        """Download a received file into folder. Removes it from Unread on success."""
        item = self.store.get_file(ref)
        if item is None:
            raise ValueError("This file is no longer in Unread.")
        if self.bot is None or self.status != CONNECTED:
            raise RuntimeError("The bot is not connected. Try again when the status is 'connected'.")
        folder.mkdir(parents=True, exist_ok=True)
        path, handle = reserve_unique_file(folder, sanitize_filename(item.file_name))
        try:
            with handle:
                await self.bot.download(item.file_id, destination=handle, timeout=300)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.store.delete_file(ref)
        log.info("Saved file #%s to %s", ref, path)
        self._on_change()
        return path

    def delete_file(self, ref: int) -> None:
        self.store.delete_file(ref)
        self._on_change()

    # ------------------------------------------------------------- lifecycle
    async def start(self, token: str | None) -> None:
        """Start background tasks. Without a token the bot stays offline."""
        await self.stop()
        self._wake = asyncio.Event()
        self.expire_due()
        self.resume_due_delays()
        self._tasks.append(asyncio.create_task(self._expiry_loop(), name="botdesk-expiry"))
        if not token:
            self._set_status(OFFLINE)
            return
        from aiogram import Bot

        self.bot = Bot(token=token)
        self._set_status(RECONNECTING)
        self._tasks.append(asyncio.create_task(self._poll_loop(), name="botdesk-poll"))
        self._tasks.append(asyncio.create_task(self._outbox_loop(), name="botdesk-outbox"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        if self.bot is not None:
            try:
                await self.bot.session.close()
            except Exception:
                log.exception("Error while closing the Telegram session")
            self.bot = None
        self.bot_username = None
        self._set_status(OFFLINE)

    async def _expiry_loop(self) -> None:
        while True:
            try:
                self.expire_due()
                self.resume_due_delays()
            except Exception:
                log.exception("Expiry check failed")
            await asyncio.sleep(1)

    async def _poll_loop(self) -> None:
        from aiogram.exceptions import (
            TelegramConflictError,
            TelegramRetryAfter,
            TelegramUnauthorizedError,
        )

        backoff = 1
        offset_text = self.store.get("update_offset")
        offset = int(offset_text) if offset_text else None
        while True:
            try:
                if self.bot_username is None:
                    me = await self.bot.get_me()
                    self.bot_username = me.username
                    await self.bot.delete_webhook(drop_pending_updates=False)
                    log.info("Logged in as @%s", me.username)
                updates = await self.bot.get_updates(
                    offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message"],
                    request_timeout=POLL_TIMEOUT + 15,
                )
                self._set_status(CONNECTED)
                self._wake.set()
                backoff = 1
                for update in updates:
                    try:
                        self._process_update(update)
                    except Exception as exc:
                        attempts = self._failed_updates.get(update.update_id, 0) + 1
                        if attempts < MAX_UPDATE_ATTEMPTS:
                            # Keep the offset where it is and let the retry path
                            # below ask Telegram for this update again.
                            self._failed_updates[update.update_id] = attempts
                            log.warning(
                                "Update %s failed (attempt %s of %s): %s",
                                update.update_id, attempts, MAX_UPDATE_ATTEMPTS, type(exc).__name__,
                            )
                            raise
                        log.error(
                            "Skipping update %s after %s failed attempts; its changes were not saved",
                            update.update_id, attempts, exc_info=exc,
                        )
                        self._failed_updates.pop(update.update_id, None)
                        self._skip_update(update.update_id)
                        self._on_error(
                            f"BotDesk skipped an update that failed {attempts} times. "
                            "Details are in the log."
                        )
                    offset = update.update_id + 1
            except asyncio.CancelledError:
                raise
            except TelegramUnauthorizedError:
                log.error("Telegram rejected the bot token")
                self._set_status(OFFLINE)
                self._on_error("Telegram rejected the bot token. Paste a valid token in Settings.")
                return
            except TelegramRetryAfter as exc:
                self._set_status(RECONNECTING)
                await asyncio.sleep(exc.retry_after)
            except TelegramConflictError:
                log.warning("Another program is using this bot token (getUpdates conflict)")
                self._set_status(RECONNECTING)
                self._on_error("Another program is receiving updates for this bot. Retrying.")
                await asyncio.sleep(10)
            except Exception as exc:
                log.warning("Connection problem: %s: %s", type(exc).__name__, exc)
                self._set_status(RECONNECTING)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _process_update(self, update) -> None:
        """Apply one Telegram update atomically.

        Chat/file changes, queued replies and the polling offset commit together.
        If anything fails, the whole update is rolled back.
        """
        with self.store.transaction():
            if update.message is not None:
                self.handle(incoming_from_message(update.message))
            self.store.set("update_offset", str(update.update_id + 1))
        self._failed_updates.pop(update.update_id, None)

    def _skip_update(self, update_id: int) -> None:
        """Advance the saved offset past an update that is being given up on, and count it."""
        with self.store.transaction():
            self.store.set("update_offset", str(update_id + 1))
            skipped = int(self.store.get("skipped_updates") or 0) + 1
            self.store.set("skipped_updates", str(skipped))
        self._on_change()

    async def _outbox_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self.status != CONNECTED:
                continue
            try:
                await self._flush_outbox()
                await self._sync_menu_if_needed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Sending paused: %s", type(exc).__name__)

    async def _flush_outbox(self) -> None:
        from aiogram.exceptions import (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramRetryAfter,
        )

        while True:
            items = self.store.pending_outbox()
            if not items:
                return
            for item in items:
                try:
                    if item.keyboard:
                        await self.bot.send_message(item.chat_id, item.text, reply_markup=_markup(item.keyboard))
                    else:
                        await self.bot.send_message(item.chat_id, item.text)
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                    return
                except (TelegramForbiddenError, TelegramBadRequest, TelegramNotFound) as exc:
                    log.warning("Dropped a message to chat %s: %s", item.chat_id, exc.message)
                    self._on_error(f"Could not deliver a message to chat {item.chat_id}: {exc.message}")
                self.store.delete_outbox(item.id)

    async def _sync_menu_if_needed(self) -> None:
        if self.store.get("menu_dirty") != "1":
            return
        if self.store.get("sync_commands") != "1":
            self.store.delete_setting("menu_dirty")
            return
        from aiogram.types import BotCommand

        commands = [BotCommand(command=c.name, description=c.name) for c in self.store.list_commands()]
        await self.bot.set_my_commands(commands[:100])
        self.store.delete_setting("menu_dirty")
        log.info("Telegram command menu updated (%d commands)", len(commands[:100]))
