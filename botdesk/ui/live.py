"""Live / Unread: live chat sub-chats and received files."""

from __future__ import annotations

import time
from datetime import datetime

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from botdesk.engine import TELEGRAM_DOWNLOAD_LIMIT
from botdesk.logsetup import redact
from botdesk.store import WAITING, Answer, Chat, FileItem
from botdesk.ui.dialogs import ConfirmDialog
from botdesk.ui.menu import _highlighted_id, _keep_highlight


def _remaining(chat: Chat) -> str:
    seconds = max(0, int(chat.expires_at - time.time()))
    return f"{seconds // 60}:{seconds % 60:02d} left"


def _chat_summary(chat: Chat) -> str:
    text = f"{'* ' if chat.unread else '  '}{chat.user_label} - {chat.state}"
    if chat.state == WAITING:
        text += f" ({_remaining(chat)})"
    return text


def _size(size: int | None) -> str:
    if size is None:
        return "unknown size"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class LiveScreen(Screen):
    BINDINGS = [
        Binding("s", "save", "Save file"),
        Binding("d", "delete", "Delete"),
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Live chats  -  Enter: open   (* = unread)", classes="section")
        yield OptionList(id="chats", markup=False)
        yield Static("Received files  -  s: save   d: delete", classes="section")
        yield OptionList(id="files", markup=False)
        yield Static("Answers  -  d: delete", classes="section")
        yield OptionList(id="answers", markup=False)
        yield Static("", id="details", markup=False)
        yield Static(
            "Known limit: Telegram lets bots download files up to 20 MB. BotDesk does not check sizes.",
            classes="hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        # Opening Live / Unread marks skipped updates as seen.
        self.app.store.delete_setting("skipped_updates")
        self.refresh_data()
        self.query_one("#chats", OptionList).focus()
        self.set_interval(1, self._tick)

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def _tick(self) -> None:
        if any(c.state == WAITING for c in self.app.store.list_chats()):
            self.refresh_data()

    def refresh_data(self) -> None:
        store = self.app.store
        chats = self.query_one("#chats", OptionList)
        _keep_highlight(
            chats,
            [Option(_chat_summary(c), id=str(c.id)) for c in store.list_chats()],
            _highlighted_id(chats),
        )
        files = self.query_one("#files", OptionList)
        file_options = []
        for item in store.list_files():
            label = f"{item.file_name}  -  from {item.user_label}"
            if item.caption:
                first_line = item.caption.splitlines()[0]
                label += f'  -  "{first_line[:50]}{"..." if len(first_line) > 50 else ""}"'
            file_options.append(Option(label, id=str(item.id)))
        _keep_highlight(files, file_options, _highlighted_id(files))
        if not file_options:
            files.add_option(Option("(no files)", id="none", disabled=True))
        answers_list = self.query_one("#answers", OptionList)
        answer_options = [
            Option(f"{a.user_label}: {a.answer[:60]}", id=str(a.id)) for a in store.list_answers()
        ]
        _keep_highlight(answers_list, answer_options, _highlighted_id(answers_list))
        if not answer_options:
            answers_list.add_option(Option("(no answers)", id="none", disabled=True))
        if chats.option_count == 0:
            chats.add_option(Option("(no live chats)", id="none", disabled=True))
        self._show_details()

    def _selected_answer(self) -> Answer | None:
        option_id = _highlighted_id(self.query_one("#answers", OptionList))
        if not option_id or option_id == "none":
            return None
        return self.app.store.get_answer(int(option_id))

    def _selected_file(self) -> FileItem | None:
        option_id = _highlighted_id(self.query_one("#files", OptionList))
        if not option_id or option_id == "none":
            return None
        return self.app.store.get_file(int(option_id))

    def _show_details(self) -> None:
        details = self.query_one("#details", Static)
        item = self._selected_file() if self.focused is self.query_one("#files") else None
        if item is None:
            details.update("")
            return
        lines = [
            f"File name: {item.file_name}",
            f"Caption:   {item.caption or '(none)'}",
            f"From:      {item.user_label}",
            f"Size:      {_size(item.file_size)}   Received: {_when(item.created_at)}",
        ]
        if item.file_size and item.file_size > TELEGRAM_DOWNLOAD_LIMIT:
            lines.append("This file is larger than 20 MB; Telegram will probably refuse the download.")
        details.update("\n".join(lines))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_details()

    def on_descendant_focus(self) -> None:
        self._show_details()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "none":
            return
        if event.option_list.id == "chats":
            self.app.push_screen(ChatScreen(int(event.option.id)))
        else:
            self.action_save()

    def action_save(self) -> None:
        item = self._selected_file()
        if item is None:
            self.notify("Select a file in 'Received files' first.", severity="warning")
            return
        self.run_worker(self._save(item.id), exclusive=True, group="save")

    async def _save(self, ref: int) -> None:
        folder = self.app.engine.files_folder(self.app.default_files_folder)
        self.notify("Downloading...", timeout=3)
        try:
            path = await self.app.engine.save_file(ref, folder)
        except Exception as exc:
            message = redact(getattr(exc, "message", None) or str(exc) or type(exc).__name__)
            if "too big" in message.lower():
                message += " (Telegram bots can download files up to 20 MB.)"
            self.notify(f"Could not save the file: {message}", severity="error", timeout=10, markup=False)
            return
        self.notify(f"Saved to {path}", markup=False, timeout=8)

    def action_delete(self) -> None:
        if self.focused is self.query_one("#files"):
            item = self._selected_file()
            if item is None:
                return

            def deleted(confirmed: bool | None) -> None:
                if confirmed:
                    self.app.engine.delete_file(item.id)

            self.app.push_screen(
                ConfirmDialog(f"Delete '{item.file_name}' from Unread? It will not be saved.", "Delete"), deleted
            )
            return
        if self.focused is self.query_one("#answers"):
            answer = self._selected_answer()
            if answer is None:
                return

            def answer_deleted(confirmed: bool | None) -> None:
                if confirmed:
                    self.app.store.delete_answer(answer.id)
                    self.app.refresh_views()

            self.app.push_screen(
                ConfirmDialog(f"Delete the answer from {answer.user_label}?", "Delete"), answer_deleted
            )
            return
        option_id = _highlighted_id(self.query_one("#chats", OptionList))
        if not option_id or option_id == "none":
            return
        chat = self.app.store.get_chat(int(option_id))
        if chat is None:
            self.refresh_data()
            return
        if chat.is_open:
            self.notify("Open the chat and close it first (ctrl+l).", severity="warning")
            return

        def removed(confirmed: bool | None) -> None:
            if confirmed:
                self.app.store.delete_chat(chat.id)
                self.app.refresh_views()

        self.app.push_screen(ConfirmDialog(f"Remove the {chat.state} chat with {chat.user_label}?", "Remove"), removed)


class ChatScreen(Screen):
    BINDINGS = [
        Binding("ctrl+l", "close_chat", "Close chat", priority=True),
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, ref: int) -> None:
        super().__init__()
        self.ref = ref
        self._shown = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="chat-info", classes="section", markup=False)
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        yield Input(placeholder="Type a reply and press Enter", id="reply", max_length=4096)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#reply", Input).focus()
        self.set_interval(1, self._tick)

    def _tick(self) -> None:
        chat = self.app.store.get_chat(self.ref)
        if chat is not None and chat.state == WAITING:
            self._update_info(chat)

    def _update_info(self, chat: Chat) -> None:
        info = f"Live chat with {chat.user_label}  -  {chat.state}"
        if chat.state == WAITING:
            info += f" ({_remaining(chat)}; your first reply accepts it)"
        self.query_one("#chat-info", Static).update(info)

    def refresh_data(self) -> None:
        store = self.app.store
        chat = store.get_chat(self.ref)
        if chat is None:
            self.app.pop_screen()
            return
        self._update_info(chat)
        messages = store.chat_messages(self.ref)
        log = self.query_one("#log", RichLog)
        for message in messages[self._shown:]:
            who, style = ("You", "bold green") if message.sender == "owner" else (chat.user_label, "bold cyan")
            stamp = datetime.fromtimestamp(message.created_at).strftime("%H:%M")
            log.write(Text.assemble((stamp, "dim"), " ", (who, style), ": ", message.text))
        self._shown = len(messages)
        reply = self.query_one("#reply", Input)
        reply.disabled = not chat.is_open
        if not chat.is_open:
            reply.placeholder = f"This chat is {chat.state}. Replies are not possible."
        if chat.unread:
            store.set_chat_unread(self.ref, False)
            self.app.refresh_views()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        try:
            self.app.engine.owner_reply(self.ref, text)
        except ValueError as exc:
            self.notify(str(exc), severity="warning")
            return
        event.input.value = ""

    def action_close_chat(self) -> None:
        chat = self.app.store.get_chat(self.ref)
        if chat is None or not chat.is_open:
            self.notify("This chat is not open.", severity="warning")
            return

        def closed(confirmed: bool | None) -> None:
            if confirmed:
                self.app.engine.close_chat(self.ref)

        self.app.push_screen(
            ConfirmDialog(f"Close the chat with {chat.user_label}? They will get the 'Chat closed' message.", "Close"),
            closed,
        )
