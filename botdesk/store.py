"""SQLite storage for settings, commands, live chats, files and the outbox."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SEND_MESSAGE = "send_message"
LIVE_CHAT = "live_chat"
WAIT_FILE = "wait_file"
ASK_QUESTION = "ask_question"
ASK_BUTTONS = "ask_buttons"
WAIT_DELAY = "wait_delay"
ACTIONS = {
    SEND_MESSAGE: "send message",
    LIVE_CHAT: "request to live chat",
    WAIT_FILE: "wait for file",
    ASK_QUESTION: "ask a question",
    ASK_BUTTONS: "ask with buttons",
    WAIT_DELAY: "wait for a delay",
}
# A pending flow's kind is the action that paused it.
PAUSING_ACTIONS = (WAIT_FILE, ASK_QUESTION, ASK_BUTTONS, WAIT_DELAY)

WAITING = "waiting"
ACCEPTED = "accepted"
EXPIRED = "expired"
CLOSED = "closed"
OPEN_STATES = (WAITING, ACCEPTED)

DEFAULT_SETTINGS = {
    "bot_name": "",
    "msg_connecting": "Connecting you with the bot's owner. Please wait.",
    "msg_expired": "Sorry, the bot's owner did not answer in time. The request expired.",
    "msg_closed": "The chat was closed.",
    "msg_unknown": "Unknown command.",
    "live_window": "600",
    "files_folder": "",
    "sync_commands": "0",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    action TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    user_label TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    unread INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    chat_ref INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    user_label TEXT NOT NULL,
    file_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    file_size INTEGER,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flows (
    chat_id INTEGER PRIMARY KEY,
    command_id INTEGER NOT NULL REFERENCES commands(id) ON DELETE CASCADE,
    step_id INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'wait_file',
    due_at REAL
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    keyboard TEXT
);
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    user_label TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at REAL NOT NULL,
    unread INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_steps_command ON steps(command_id, position);
CREATE INDEX IF NOT EXISTS idx_chats_chat ON chats(chat_id, state);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON chat_messages(chat_ref);
"""


@dataclass(frozen=True)
class Command:
    id: int
    name: str


@dataclass(frozen=True)
class Step:
    id: int
    command_id: int
    position: int
    action: str
    text: str


@dataclass(frozen=True)
class Chat:
    id: int
    chat_id: int
    user_label: str
    state: str
    created_at: float
    expires_at: float
    updated_at: float
    unread: bool

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES


@dataclass(frozen=True)
class ChatMessage:
    id: int
    sender: str  # "user" or "owner"
    text: str
    created_at: float


@dataclass(frozen=True)
class FileItem:
    id: int
    chat_id: int
    user_label: str
    file_id: str
    file_name: str
    caption: str
    file_size: int | None
    created_at: float


@dataclass(frozen=True)
class Answer:
    id: int
    chat_id: int
    user_label: str
    question: str
    answer: str
    created_at: float
    unread: bool


@dataclass(frozen=True)
class OutboxItem:
    id: int
    chat_id: int
    text: str
    keyboard: str | None = None


APPLICATION_ID = 0x42444B31  # "BDK1": marks databases created by this BotDesk


class IncompatibleDatabase(Exception):
    """The file is a database made by some other program; it is left untouched."""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        try:
            self._check_owner(path)
        except BaseException:
            self.conn.close()
            raise
        self._tx_depth = 0
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns that older profiles lack. Existing rows keep their values."""
        additions = {
            "flows": {"kind": "TEXT NOT NULL DEFAULT 'wait_file'", "due_at": "REAL"},
            "outbox": {"keyboard": "TEXT"},
        }
        for table, columns in additions.items():
            present = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in present:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _check_owner(self, path: Path | str) -> None:
        """Refuse a non-empty database that this BotDesk did not create."""
        app_id = self.conn.execute("PRAGMA application_id").fetchone()[0]
        has_tables = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
        ).fetchone()
        if has_tables and app_id != APPLICATION_ID:
            raise IncompatibleDatabase(
                f"{path} was not created by this version of BotDesk (another program or "
                "an older build uses it). It was not changed. Move it away, or set the "
                "BOTDESK_HOME environment variable to a different folder."
            )

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit everything inside the block together, or roll all of it back.

        Re-entrant: a nested call joins the outer transaction.
        """
        if self._tx_depth:
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._tx_depth = 1
        try:
            yield
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()
        finally:
            self._tx_depth = 0

    def _commit(self) -> None:
        if not self._tx_depth:
            self.conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self._commit()
        return cur

    def has_bot_state(self) -> bool:
        """True if the profile holds chats, files, queued replies or a Telegram offset."""
        for sql in (
            "SELECT 1 FROM chats LIMIT 1",
            "SELECT 1 FROM files LIMIT 1",
            "SELECT 1 FROM outbox LIMIT 1",
            "SELECT 1 FROM settings WHERE key = 'update_offset'",
        ):
            if self.conn.execute(sql).fetchone() is not None:
                return True
        return False

    # ----- settings -----
    def get(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return DEFAULT_SETTINGS.get(key, "")
        return row["value"]

    def set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def delete_setting(self, key: str) -> None:
        self._exec("DELETE FROM settings WHERE key = ?", (key,))

    def has_setting(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone() is not None

    # ----- commands and steps -----
    def list_commands(self) -> list[Command]:
        rows = self.conn.execute("SELECT id, name FROM commands ORDER BY name").fetchall()
        return [Command(r["id"], r["name"]) for r in rows]

    def get_command(self, command_id: int) -> Command | None:
        r = self.conn.execute("SELECT id, name FROM commands WHERE id = ?", (command_id,)).fetchone()
        return Command(r["id"], r["name"]) if r else None

    def get_command_by_name(self, name: str) -> Command | None:
        r = self.conn.execute("SELECT id, name FROM commands WHERE name = ?", (name,)).fetchone()
        return Command(r["id"], r["name"]) if r else None

    def add_command(self, name: str) -> int:
        return self._exec("INSERT INTO commands(name) VALUES(?)", (name,)).lastrowid

    def rename_command(self, command_id: int, name: str) -> None:
        self._exec("UPDATE commands SET name = ? WHERE id = ?", (name, command_id))

    def delete_command(self, command_id: int) -> None:
        self._exec("DELETE FROM commands WHERE id = ?", (command_id,))

    def list_steps(self, command_id: int) -> list[Step]:
        rows = self.conn.execute(
            "SELECT * FROM steps WHERE command_id = ? ORDER BY position, id", (command_id,)
        ).fetchall()
        return [Step(r["id"], r["command_id"], r["position"], r["action"], r["text"]) for r in rows]

    def add_step(self, command_id: int, action: str, text: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM steps WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        return self._exec(
            "INSERT INTO steps(command_id, position, action, text) VALUES(?, ?, ?, ?)",
            (command_id, row["pos"], action, text),
        ).lastrowid

    def update_step(self, step_id: int, action: str, text: str) -> None:
        self._exec("UPDATE steps SET action = ?, text = ? WHERE id = ?", (action, text, step_id))

    def delete_step(self, step_id: int) -> None:
        self._exec("DELETE FROM steps WHERE id = ?", (step_id,))

    def move_step(self, step_id: int, delta: int) -> bool:
        """Move a step up (delta=-1) or down (delta=+1). Returns True if moved."""
        row = self.conn.execute("SELECT command_id FROM steps WHERE id = ?", (step_id,)).fetchone()
        if row is None:
            return False
        steps = self.list_steps(row["command_id"])
        index = next(i for i, s in enumerate(steps) if s.id == step_id)
        target = index + delta
        if not 0 <= target < len(steps):
            return False
        steps[index], steps[target] = steps[target], steps[index]
        for pos, step in enumerate(steps):
            self.conn.execute("UPDATE steps SET position = ? WHERE id = ?", (pos, step.id))
        self._commit()
        return True

    # ----- flows (a command paused on a wait step) -----
    def get_flow(self, chat_id: int) -> tuple[int, int] | None:
        r = self.conn.execute(
            "SELECT command_id, step_id FROM flows WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return (r["command_id"], r["step_id"]) if r else None

    def get_flow_full(self, chat_id: int) -> sqlite3.Row | None:
        """The pending flow with its kind and due time, or None."""
        return self.conn.execute("SELECT * FROM flows WHERE chat_id = ?", (chat_id,)).fetchone()

    def set_flow(self, chat_id: int, command_id: int, step_id: int, kind: str = WAIT_FILE,
                 due_at: float | None = None) -> None:
        self._exec(
            "INSERT INTO flows(chat_id, command_id, step_id, kind, due_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET command_id = excluded.command_id, "
            "step_id = excluded.step_id, kind = excluded.kind, due_at = excluded.due_at",
            (chat_id, command_id, step_id, kind, due_at),
        )

    def clear_flow(self, chat_id: int) -> None:
        self._exec("DELETE FROM flows WHERE chat_id = ?", (chat_id,))

    def due_delay_flows(self, now: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM flows WHERE kind = ? AND due_at IS NOT NULL AND due_at <= ?",
            (WAIT_DELAY, now),
        ).fetchall()

    # ----- answers to "ask a question" and "ask with buttons" -----
    def add_answer(self, chat_id: int, user_label: str, question: str, answer: str, now: float) -> int:
        return self._exec(
            "INSERT INTO answers(chat_id, user_label, question, answer, created_at, unread) "
            "VALUES(?, ?, ?, ?, ?, 1)",
            (chat_id, user_label, question, answer, now),
        ).lastrowid

    @staticmethod
    def _answer(r: sqlite3.Row) -> Answer:
        return Answer(
            r["id"], r["chat_id"], r["user_label"], r["question"], r["answer"],
            r["created_at"], bool(r["unread"]),
        )

    def list_answers(self) -> list[Answer]:
        rows = self.conn.execute("SELECT * FROM answers ORDER BY id DESC").fetchall()
        return [self._answer(r) for r in rows]

    def get_answer(self, ref: int) -> Answer | None:
        r = self.conn.execute("SELECT * FROM answers WHERE id = ?", (ref,)).fetchone()
        return self._answer(r) if r else None

    def delete_answer(self, ref: int) -> None:
        self._exec("DELETE FROM answers WHERE id = ?", (ref,))

    # ----- live chats -----
    @staticmethod
    def _chat(r: sqlite3.Row) -> Chat:
        return Chat(
            r["id"], r["chat_id"], r["user_label"], r["state"],
            r["created_at"], r["expires_at"], r["updated_at"], bool(r["unread"]),
        )

    def get_chat(self, ref: int) -> Chat | None:
        r = self.conn.execute("SELECT * FROM chats WHERE id = ?", (ref,)).fetchone()
        return self._chat(r) if r else None

    def open_chat_for(self, chat_id: int) -> Chat | None:
        r = self.conn.execute(
            "SELECT * FROM chats WHERE chat_id = ? AND state IN (?, ?) ORDER BY id DESC LIMIT 1",
            (chat_id, *OPEN_STATES),
        ).fetchone()
        return self._chat(r) if r else None

    def create_chat(self, chat_id: int, user_label: str, now: float, expires_at: float) -> Chat:
        ref = self._exec(
            "INSERT INTO chats(chat_id, user_label, state, created_at, expires_at, updated_at, unread) "
            "VALUES(?, ?, ?, ?, ?, ?, 1)",
            (chat_id, user_label, WAITING, now, expires_at, now),
        ).lastrowid
        return self.get_chat(ref)

    def list_chats(self) -> list[Chat]:
        rows = self.conn.execute(
            "SELECT * FROM chats ORDER BY CASE WHEN state IN (?, ?) THEN 0 ELSE 1 END, "
            "updated_at DESC, id DESC",
            OPEN_STATES,
        ).fetchall()
        return [self._chat(r) for r in rows]

    def set_chat_state(self, ref: int, state: str, now: float) -> None:
        self._exec("UPDATE chats SET state = ?, updated_at = ? WHERE id = ?", (state, now, ref))

    def set_chat_unread(self, ref: int, unread: bool) -> None:
        self._exec("UPDATE chats SET unread = ? WHERE id = ?", (int(unread), ref))

    def update_chat_label(self, ref: int, label: str) -> None:
        self._exec("UPDATE chats SET user_label = ? WHERE id = ?", (label, ref))

    def due_waiting_chats(self, now: float) -> list[Chat]:
        rows = self.conn.execute(
            "SELECT * FROM chats WHERE state = ? AND expires_at <= ?", (WAITING, now)
        ).fetchall()
        return [self._chat(r) for r in rows]

    def delete_chat(self, ref: int) -> None:
        self._exec("DELETE FROM chats WHERE id = ?", (ref,))

    def add_chat_message(self, ref: int, sender: str, text: str, now: float) -> None:
        self.conn.execute(
            "INSERT INTO chat_messages(chat_ref, sender, text, created_at) VALUES(?, ?, ?, ?)",
            (ref, sender, text, now),
        )
        self.conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, ref))
        self._commit()

    def chat_messages(self, ref: int) -> list[ChatMessage]:
        rows = self.conn.execute(
            "SELECT * FROM chat_messages WHERE chat_ref = ? ORDER BY id", (ref,)
        ).fetchall()
        return [ChatMessage(r["id"], r["sender"], r["text"], r["created_at"]) for r in rows]

    # ----- files -----
    def add_file(
        self, chat_id: int, user_label: str, file_id: str, file_name: str,
        caption: str, file_size: int | None, now: float,
    ) -> FileItem:
        ref = self._exec(
            "INSERT INTO files(chat_id, user_label, file_id, file_name, caption, file_size, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_label, file_id, file_name, caption, file_size, now),
        ).lastrowid
        return self.get_file(ref)

    @staticmethod
    def _file(r: sqlite3.Row) -> FileItem:
        return FileItem(
            r["id"], r["chat_id"], r["user_label"], r["file_id"], r["file_name"],
            r["caption"], r["file_size"], r["created_at"],
        )

    def get_file(self, ref: int) -> FileItem | None:
        r = self.conn.execute("SELECT * FROM files WHERE id = ?", (ref,)).fetchone()
        return self._file(r) if r else None

    def list_files(self) -> list[FileItem]:
        rows = self.conn.execute("SELECT * FROM files ORDER BY id DESC").fetchall()
        return [self._file(r) for r in rows]

    def delete_file(self, ref: int) -> None:
        self._exec("DELETE FROM files WHERE id = ?", (ref,))

    # ----- outbox -----
    def enqueue(self, chat_id: int, text: str, now: float, keyboard: str | None = None) -> None:
        self._exec(
            "INSERT INTO outbox(chat_id, text, created_at, keyboard) VALUES(?, ?, ?, ?)",
            (chat_id, text, now, keyboard),
        )

    def pending_outbox(self, limit: int = 20) -> list[OutboxItem]:
        rows = self.conn.execute("SELECT * FROM outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
        return [OutboxItem(r["id"], r["chat_id"], r["text"], r["keyboard"]) for r in rows]

    def delete_outbox(self, ref: int) -> None:
        self._exec("DELETE FROM outbox WHERE id = ?", (ref,))

    # ----- badge -----
    def unread_count(self) -> int:
        chats = self.conn.execute("SELECT COUNT(*) FROM chats WHERE unread = 1").fetchone()[0]
        files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        answers = self.conn.execute("SELECT COUNT(*) FROM answers WHERE unread = 1").fetchone()[0]
        return chats + files + answers
