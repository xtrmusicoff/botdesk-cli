"""Reusable modal dialogs."""

from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, TextArea

from botdesk.store import ACTIONS, ASK_BUTTONS, SEND_MESSAGE, WAIT_DELAY
from botdesk.validation import parse_buttons, parse_delay

# Actions whose step text is shown in the text box.
TEXT_ACTIONS = {SEND_MESSAGE, "ask_question", ASK_BUTTONS, WAIT_DELAY}


class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, question: str, yes_label: str = "Yes") -> None:
        super().__init__()
        self.question = question
        self.yes_label = yes_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.question, markup=False, classes="dialog-text")
            with Horizontal(classes="buttons"):
                yield Button(self.yes_label, id="yes", variant="error")
                yield Button("No", id="no")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class TextPromptDialog(ModalScreen[str | None]):
    """One-line input. `validator` returns an error message or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        value: str = "",
        validator: Callable[[str], str | None] | None = None,
        placeholder: str = "",
        max_length: int = 0,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.validator = validator
        self.placeholder = placeholder
        self.max_length = max_length

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, markup=False)
            yield Input(self.value, placeholder=self.placeholder, max_length=self.max_length, id="value")
            yield Static("", id="error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def _submit(self) -> None:
        value = self.query_one("#value", Input).value
        error = self.validator(value) if self.validator else None
        if error:
            self.query_one("#error", Static).update(error)
            return
        self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class StepDialog(ModalScreen[tuple[str, str] | None]):
    """Choose an action and (for send message) its text."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, action: str = SEND_MESSAGE, text: str = "") -> None:
        super().__init__()
        self.action_value = action
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Action")
            yield Select(
                [(label, key) for key, label in ACTIONS.items()],
                value=self.action_value,
                allow_blank=False,
                id="action",
            )
            with Vertical(id="text-box"):
                yield Label("Message text", id="text-label")
                yield TextArea(self.text, id="text")
            yield Static("", id="hint", classes="hint")
            yield Static("", id="error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Save (ctrl+s)", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self._sync_visibility(self.action_value)

    def _sync_visibility(self, action: str) -> None:
        self.query_one("#text-box").display = action in TEXT_ACTIONS
        hints = {
            SEND_MESSAGE: "Sends this text to the user.",
            "live_chat": "Opens a live chat and sends the \"Connecting you with the bot's owner\" message (Settings).",
            "wait_file": "Waits until the user sends a file. Later steps run after the file arrives.",
            "ask_question": "Sends the first line as the question. The user's next text message is saved to Live / Unread.",
            "ask_buttons": "First line is the question. Each following line is one button. Pressing a button saves it to Live / Unread.",
            "wait_delay": "Enter a number of seconds, from 1 to 86400. Later steps run after the delay.",
        }
        self.query_one("#hint", Static).update(hints.get(action, ""))
        label = "Seconds" if action == WAIT_DELAY else "Message text"
        self.query_one("#text-label", Label).update(label)

    def on_select_changed(self, event: Select.Changed) -> None:
        self._sync_visibility(str(event.value))

    def action_save(self) -> None:
        action = str(self.query_one("#action", Select).value)
        text = self.query_one("#text", TextArea).text if action in TEXT_ACTIONS else ""
        error = self.query_one("#error", Static)
        if action in TEXT_ACTIONS and not text.strip():
            error.update("This step needs text.")
            return
        if action == WAIT_DELAY and parse_delay(text) is None:
            error.update("Enter a whole number of seconds from 1 to 86400.")
            return
        if action == ASK_BUTTONS:
            question, options = parse_buttons(text)
            if not question or not options:
                error.update("Write the question on the first line and at least one button on the next lines.")
                return
        self.dismiss((action, text))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.dismiss(None)


class ChangePasswordDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Change master password")
            yield Input(password=True, placeholder="Current password", id="current")
            yield Input(password=True, placeholder="New password (at least 8 characters)", id="new")
            yield Input(password=True, placeholder="Repeat new password", id="repeat")
            yield Static("", id="error", classes="error", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Change", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#current", Input).focus()

    async def _submit(self) -> None:
        from botdesk.validation import MIN_PASSWORD_LENGTH

        current = self.query_one("#current", Input).value
        new = self.query_one("#new", Input).value
        repeat = self.query_one("#repeat", Input).value
        error = self.query_one("#error", Static)
        if len(new) < MIN_PASSWORD_LENGTH:
            error.update(f"The new password must have at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if new != repeat:
            error.update("The new passwords do not match.")
            return
        error.update("Working...")
        problem = await self.app.change_password(current, new)
        if problem:
            error.update(problem)
            return
        self.dismiss(True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        order = ["current", "new", "repeat"]
        index = order.index(event.input.id)
        if index < 2:
            self.query_one(f"#{order[index + 1]}", Input).focus()
        else:
            await self._submit()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            await self._submit()
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
