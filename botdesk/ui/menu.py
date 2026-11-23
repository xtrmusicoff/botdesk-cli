"""Main menu and Commands screens."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from botdesk import __version__
from botdesk.engine import CONNECTED, RECONNECTING
from botdesk.store import ACTIONS, SEND_MESSAGE
from botdesk.ui.dialogs import ConfirmDialog, StepDialog, TextPromptDialog
from botdesk.validation import command_name_error


def _keep_highlight(option_list: OptionList, options: list[Option], previous_id: str | None) -> None:
    """Replace the options and restore the highlight by option id."""
    index = option_list.highlighted
    option_list.clear_options()
    option_list.add_options(options)
    if not options:
        return
    ids = [o.id for o in options]
    if previous_id in ids:
        option_list.highlighted = ids.index(previous_id)
    else:
        option_list.highlighted = min(index or 0, len(options) - 1)


def _highlighted_id(option_list: OptionList) -> str | None:
    option = option_list.highlighted_option
    return option.id if option else None


class MainMenuScreen(Screen):
    BINDINGS = [
        Binding("c", "open('commands')", "Commands"),
        Binding("l", "open('live')", "Live / Unread"),
        Binding("s", "open('settings')", "Settings"),
        Binding("q", "open('quit')", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield OptionList(
            Option("Commands", id="commands"),
            Option("Live / Unread", id="live"),
            Option("Settings", id="settings"),
            Option("Quit", id="quit"),
            id="menu",
            markup=False,
        )
        yield Static("", id="status")
        yield Static("", id="profile", classes="hint", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#menu", OptionList).focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        unread = self.app.store.unread_count()
        skipped = int(self.app.store.get("skipped_updates") or 0)
        label = f"Live / Unread ({unread})" if unread else "Live / Unread"
        if skipped:
            label += f"  -  Skipped updates: {skipped}"
        self.query_one("#menu", OptionList).replace_option_prompt("live", label)
        self.query_one("#profile", Static).update(
            f"BotDesk {__version__}   Profile: {self.app.data_path}"
        )
        status = self.app.engine.status
        style = {CONNECTED: "bold green", RECONNECTING: "bold yellow"}.get(status, "bold red")
        line = Text.assemble("Status: ", (status, style))
        if not self.app.has_token:
            line.append("  (no bot token - paste it in Settings)", style="dim")
        self.query_one("#status", Static).update(line)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.run_action(f"open('{event.option.id}')")

    async def action_open(self, target: str) -> None:
        from botdesk.ui.live import LiveScreen
        from botdesk.ui.settings import SettingsScreen

        if target == "commands":
            self.app.push_screen(CommandsScreen())
        elif target == "live":
            self.app.push_screen(LiveScreen())
        elif target == "settings":
            self.app.push_screen(SettingsScreen())
        elif target == "quit":
            await self.app.action_quit()


def _normalize_name(value: str) -> str:
    return value.strip().removeprefix("/")


class CommandsScreen(Screen):
    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("r", "rename", "Rename"),
        Binding("d", "delete", "Delete"),
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Commands  -  Enter: edit steps", classes="section")
        yield OptionList(id="commands", markup=False)
        yield Static("", id="empty", classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#commands", OptionList).focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        store = self.app.store
        option_list = self.query_one("#commands", OptionList)
        options = []
        for command in store.list_commands():
            count = len(store.list_steps(command.id))
            options.append(Option(f"/{command.name}   ({count} step{'s' if count != 1 else ''})", id=str(command.id)))
        _keep_highlight(option_list, options, _highlighted_id(option_list))
        self.query_one("#empty", Static).update("" if options else "No commands yet. Press 'a' to add one.")

    def _selected_id(self) -> int | None:
        option_id = _highlighted_id(self.query_one("#commands", OptionList))
        return int(option_id) if option_id else None

    def _validator(self, exclude: str | None = None):
        def validate(value: str) -> str | None:
            existing = {c.name for c in self.app.store.list_commands()} - {exclude}
            return command_name_error(_normalize_name(value), existing)

        return validate

    def action_add(self) -> None:
        def created(value: str | None) -> None:
            if value is None:
                return
            command_id = self.app.store.add_command(_normalize_name(value))
            self.app.engine.request_menu_sync()
            self.refresh_data()
            self.app.push_screen(CommandStepsScreen(command_id))

        self.app.push_screen(
            TextPromptDialog(
                "New command name (lowercase letters, digits, _; up to 32)",
                validator=self._validator(),
                placeholder="for example: start",
                max_length=33,
            ),
            created,
        )

    def action_rename(self) -> None:
        command_id = self._selected_id()
        if command_id is None:
            return
        command = self.app.store.get_command(command_id)

        def renamed(value: str | None) -> None:
            if value is None:
                return
            self.app.store.rename_command(command_id, _normalize_name(value))
            self.app.engine.request_menu_sync()
            self.refresh_data()

        self.app.push_screen(
            TextPromptDialog(f"Rename /{command.name}", command.name, self._validator(command.name), max_length=33),
            renamed,
        )

    def action_delete(self) -> None:
        command_id = self._selected_id()
        if command_id is None:
            return
        command = self.app.store.get_command(command_id)

        def deleted(confirmed: bool | None) -> None:
            if confirmed:
                self.app.store.delete_command(command_id)
                self.app.engine.request_menu_sync()
                self.refresh_data()

        self.app.push_screen(ConfirmDialog(f"Delete /{command.name} and all its steps?", "Delete"), deleted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.push_screen(CommandStepsScreen(int(event.option.id)))


class CommandStepsScreen(Screen):
    BINDINGS = [
        Binding("a", "add", "Add step"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("shift+up,left_square_bracket", "move(-1)", "Move up"),
        Binding("shift+down,right_square_bracket", "move(1)", "Move down"),
        Binding("escape", "app.pop_screen", "Back"),
    ]

    def __init__(self, command_id: int) -> None:
        super().__init__()
        self.command_id = command_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="title", classes="section", markup=False)
        yield OptionList(id="steps", markup=False)
        yield Static("", id="empty", classes="hint")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#steps", OptionList).focus()

    def refresh_data(self) -> None:
        command = self.app.store.get_command(self.command_id)
        if command is None:
            self.app.pop_screen()
            return
        self.query_one("#title", Static).update(f"Steps of /{command.name}  -  run in this order")
        options = []
        for number, step in enumerate(self.app.store.list_steps(self.command_id), start=1):
            label = f"{number}. {ACTIONS.get(step.action, step.action)}"
            if step.action == SEND_MESSAGE:
                preview = " ".join(step.text.split())
                label += f': "{preview[:60]}{"..." if len(preview) > 60 else ""}"'
            options.append(Option(label, id=str(step.id)))
        option_list = self.query_one("#steps", OptionList)
        _keep_highlight(option_list, options, _highlighted_id(option_list))
        self.query_one("#empty", Static).update("" if options else "No steps yet. Press 'a' to add one.")

    def _selected_step_id(self) -> int | None:
        option_id = _highlighted_id(self.query_one("#steps", OptionList))
        return int(option_id) if option_id else None

    def action_add(self) -> None:
        def added(result: tuple[str, str] | None) -> None:
            if result:
                step_id = self.app.store.add_step(self.command_id, *result)
                self.refresh_data()
                self._highlight(step_id)

        self.app.push_screen(StepDialog(), added)

    def action_edit(self) -> None:
        step_id = self._selected_step_id()
        if step_id is None:
            return
        step = next(s for s in self.app.store.list_steps(self.command_id) if s.id == step_id)

        def edited(result: tuple[str, str] | None) -> None:
            if result:
                self.app.store.update_step(step_id, *result)
                self.refresh_data()

        self.app.push_screen(StepDialog(step.action, step.text), edited)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_edit()

    def action_delete(self) -> None:
        step_id = self._selected_step_id()
        if step_id is None:
            return

        def deleted(confirmed: bool | None) -> None:
            if confirmed:
                self.app.store.delete_step(step_id)
                self.refresh_data()

        self.app.push_screen(ConfirmDialog("Delete this step?", "Delete"), deleted)

    def action_move(self, delta: int) -> None:
        step_id = self._selected_step_id()
        if step_id is not None and self.app.store.move_step(step_id, delta):
            self.refresh_data()
            self._highlight(step_id)

    def _highlight(self, step_id: int) -> None:
        option_list = self.query_one("#steps", OptionList)
        for index in range(option_list.option_count):
            if option_list.get_option_at_index(index).id == str(step_id):
                option_list.highlighted = index
                return
