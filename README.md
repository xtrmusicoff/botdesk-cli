# BotDesk

BotDesk is a terminal application (TUI) that runs and manages **one** Telegram bot.
You define `/commands` made of ordered steps (send a message, request a live chat, wait for a file),
answer people live from the terminal, and review the files they send in an inbox.
BotDesk stores its settings and conversations in SQLite and saves downloaded attachments as ordinary files.
Your master password encrypts the bot token only.

## Requirements

- Python 3.11 or newer (developed and tested with Python 3.14).
- Packages (see `requirements.txt`): `textual`, `aiogram`, `cryptography`. SQLite comes with Python.

Install BotDesk straight from GitHub:

```
python -m pip install git+https://github.com/xtrmusicoff/botdesk-cli.git
```

Or clone the repository and install from its folder:

```
python -m pip install -r requirements.txt
```

## Get a bot token from BotFather

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, choose a display name and a username ending in `bot`.
3. BotFather replies with a token like `123456789:AAH...`. Copy it.
4. In BotDesk open **Settings**, paste the token into **Bot token** and press **Save token** (or Enter in that field).
   The token is encrypted right away, the field is cleared, and BotDesk connects. The token is never shown again;
   Settings only says whether a token is saved. To rotate the token for the same bot, paste its new token here.

Each profile (data folder) belongs to one Telegram bot. To use a different bot, set `BOTDESK_HOME`
to a new, empty folder before starting BotDesk. To return to the original profile, restore the previous
`BOTDESK_HOME` value, or unset it if you used the default.

## First launch: the master password

On the first start BotDesk asks you to create a **master password** (at least 8 characters, typed twice).
It encrypts the bot token on this computer (scrypt key derivation + AES-GCM). The password itself is never stored.
BotDesk asks for it every time it starts.

> **Warning:** a forgotten master password cannot be recovered. On the unlock screen, **Forgot password**
> (or `ctrl+r`) deletes the saved token so you can set a new password and paste the token again.
> Commands, chats and files are kept.

You can change the password later in **Settings → Change master password** (you need the current one).

## Run

After installing, start BotDesk with either command:

```
botdesk
```

```
python -m botdesk
```

When you run from a cloned folder without installing, use `python -m botdesk` from that folder.

## Walkthrough

1. **Create a command.** Main menu → **Commands** → `a` → type `support` → Enter. The steps screen opens.
   - `a` → action **send message** → type `Hi! Send us your document, please.` → `ctrl+s`.
   - `a` → action **request to live chat** → `ctrl+s`.
   - `a` → action **wait for file** → `ctrl+s`.
   - `a` → action **send message** → `Thanks, we got your file.` → `ctrl+s`.
   - Reorder with `shift+up` / `shift+down` (or `[` / `]`), edit with `e` or Enter, delete with `d`. Changes are saved immediately.
   - `/start` is not created for you. It is a normal command, so add it the same way if you want a greeting.
2. **Test it in Telegram.** Open your bot and send `/support`. You get the first message and the
   "Connecting you with the bot's owner" message. BotDesk rings the terminal bell, shows a notification,
   and the main menu shows **Live / Unread (1)**.
3. **Open the live chat.** Main menu → **Live / Unread** (`l`). Highlight the chat under **Live chats** and press Enter.
   You see every message the user sent since the request. Type a reply and press Enter. Your first reply marks
   the request `accepted`. Press `ctrl+l` (then `y`) to close the chat; the user gets the "Chat closed" message.
   If you do not reply within the live chat window (default 600 s), the request becomes `expired` and the user
   gets the "Request expired" message.
4. **Send a file.** In Telegram, send any file (with a caption if you like). It appears under **Received files**
   with the file name exactly as sent and its caption. The command continues with its next step
   ("Thanks, we got your file.").
5. **Save or delete it.** Highlight the file (use Tab to move between lists) and press `s` (or Enter) to save it
   into the Files folder, or `d` to delete it from Unread. Either way it leaves Unread.

## Settings

| Setting | Default | Notes |
|---|---|---|
| Bot name | *(empty, shown as "Unnamed bot")* | Local label for the title `BotDesk - <bot name>`. Never sent to Telegram. |
| Bot token | *(none)* | Pasted from BotFather, stored encrypted, never shown again. Saved with **Save token**. |
| Master password | set at first launch | **Change master password** button. |
| "Connecting you with the bot's owner" | `Connecting you with the bot's owner. Please wait.` | Sent when a live chat is requested. |
| "Request expired" | `Sorry, the bot's owner did not answer in time. The request expired.` | Sent when a request expires. |
| "Chat closed" | `The chat was closed.` | Sent when you close a live chat. |
| "Unknown command" | `Unknown command.` | Reply to a `/command` that does not exist. |
| Live chat window | `600` seconds | Whole number from 30 to 86400. An invalid value shows an error and nothing is saved until it is fixed. Applies to new requests. |
| Files folder | `<data folder>\files` | Created when the first file is saved. |
| Sync Telegram command menu | off | When on, the bot's `/` menu in Telegram is replaced with your command list whenever commands are added, renamed or deleted (and when you turn it on). Each entry's description is the command name. |

Press `ctrl+s` (or **Save**) to save the form. The token has its own **Save token** button.

## Keybindings

| Where | Key | Action |
|---|---|---|
| Everywhere | `ctrl+q` | Quit |
| Everywhere | `tab` / `shift+tab` | Move focus |
| Unlock screen | `ctrl+r` | Forgot password (reset) |
| Main menu | `c` | Commands |
| Main menu | `l` | Live / Unread |
| Main menu | `s` | Settings |
| Main menu | `q` | Quit |
| Commands | `a` / `r` / `d` | Add / rename / delete command |
| Commands | `enter` | Edit the command's steps |
| Steps | `a` | Add step |
| Steps | `e` or `enter` | Edit step |
| Steps | `d` | Delete step |
| Steps | `shift+up` or `[` | Move step up |
| Steps | `shift+down` or `]` | Move step down |
| Step dialog | `ctrl+s` | Save step |
| Live / Unread | `enter` | Open chat / save file |
| Live / Unread | `s` | Save highlighted file |
| Live / Unread | `d` | Delete highlighted file, or remove a closed/expired chat |
| Chat | `enter` | Send reply |
| Chat | `ctrl+l` | Close chat |
| Settings | `ctrl+s` | Save settings |
| Dialogs | `y` / `n` | Yes / No in confirmations |
| Commands, Steps, Live / Unread, Chat, Settings, dialogs | `escape` | Back / cancel |

## How messages are handled

- A message starting with `/` is always a command, even during a live chat. An unknown command gets the
  "Unknown command" reply.
- Plain text from a user with an open live chat (`waiting` or `accepted`) is stored in that chat. Plain text from
  anyone else is ignored and not stored.
- Files are accepted only while a **wait for file** step is active for that user. Other files are ignored.
  A pending wait stays active when the user runs another command (for example `/help`), unless that command reaches
  its own **wait for file** step.
- Running a **request to live chat** step when the user already has an open chat sends the "Connecting" message
  again but does not open a second chat.
- Photos, voice messages and other media that have no file name in Telegram get a generated name
  (for example `photo_<id>.jpg`).
- Messages longer than Telegram's 4096-character limit are sent in several parts.

## Connection

The status line on the main menu shows `connected`, `reconnecting` or `offline`.
BotDesk reconnects automatically with increasing delays (up to 60 s). It shows `offline` when no token is saved
or when Telegram rejects the token.

For each incoming update, BotDesk saves the chat or file changes, any queued replies, and the next polling
offset in one SQLite transaction. If processing fails before commit, none of those changes are saved and
polling does not advance past the failed update. BotDesk retries it. After 5 failed attempts BotDesk skips the
update, logs the error with its details, and shows a notice. The changes from a skipped update are lost.
The attempt count is kept in memory, so a restart starts counting again.

Outgoing replies and notices stay in a local queue while offline. Delivery is **not exactly once**:
if Telegram accepts a message but BotDesk stops before recording that success, a retry can send it twice.
Telegram may also reject a message permanently, for example if the recipient blocked the bot.

While BotDesk is closed the bot is offline. At the next start it picks up updates still available from Telegram.
Live chat requests survive a restart. Requests whose window passed while BotDesk was closed are marked
`expired` at startup, and the others keep their remaining time.

## Where things are stored

| What | Where |
|---|---|
| Data folder (profile) | Windows: `%APPDATA%\BotDesk`. macOS: `~/Library/Application Support/BotDesk`. Linux: `$XDG_DATA_HOME/BotDesk` or `~/.local/share/BotDesk`. Set `BOTDESK_HOME` to use a different folder. |
| Database (settings, encrypted token, commands, chats, Unread items, outbox) | `<data folder>\botdesk.db` |
| Log | `<data folder>\botdesk.log` (rotated at 1 MB, 3 backups). The token is redacted from every line. |
| Saved files | The Files folder setting, by default `<data folder>\files` |

Keep the profile and any custom Files folder private. Chat history, queued replies, logs, and downloaded
attachments are readable by anyone with access to those files. Exclude them when sharing a project archive.
The project’s Git ignore rules cover untracked files in the default `data/` folder. Files already tracked
by Git stay tracked.

File names are sanitized before writing. Path separators and characters invalid on Windows are removed, and
reserved names like `CON` get a `_` prefix. If the name already exists, a suffix is added (`report_1.pdf`, `report_2.pdf`, ...).
Existing files are never overwritten.

## Known limits

- One bot only, with one owner.
- Telegram lets bots download files up to **20 MB**. BotDesk does not check sizes. Larger files appear in Unread,
  but saving them fails with an error from Telegram.
- No password recovery. A forgotten master password means pasting the token again and setting a new password.
- Saving a file needs the bot to be connected (the file is downloaded from Telegram when you press save).

## Tests

```
python -m pip install pytest pytest-asyncio
python -m pytest
```
