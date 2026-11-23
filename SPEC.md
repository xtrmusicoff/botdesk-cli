# BotDesk - SPEC

Status: confirmed through an interview. This file is the input for an AI that will build the program.
Items marked **(assumed)** were proposed by the interviewer and not contradicted. Treat them as defaults.

## 1. Overview

BotDesk is a Python terminal application (TUI) that runs and manages **one** Telegram bot.

- The owner defines `/commands`. Each command is an ordered list of steps.
- A step is one of three actions: `send message`, `request to live chat`, `wait for file`.
- The owner answers users live from the terminal and reviews received files in an inbox.

**User:** the bot owner (one person). The people who message the bot are the bot's users.
**Why:** set up bot behavior and answer people from a terminal screen, without editing scripts or reading logs.

## 2. Decisions

| Topic | Decision |
|---|---|
| Name | BotDesk |
| TUI | Textual |
| Telegram library | aiogram (async, shares one asyncio loop with Textual) |
| Bots per program | Exactly one |
| Storage | Local SQLite **(assumed)** |
| Token storage | Encrypted, unlocked by a master password typed at launch |
| Editing commands | TUI forms **(assumed)** |
| Run command | `python -m botdesk` **(assumed)** |

## 3. Out of scope

- Multiple bots, multiple owners or operators.
- Other action types: photos, shell scripts, Python hooks. (Question, buttons and delay actions were added later; see section 6.)
- File type or size validation (BotDesk does not enforce limits).
- Web UI, hosting, deployment.
- Storing plain text from users who have no open live chat and no pending question.

## 4. Main menu

- Window title: `BotDesk - <bot name>`.
  - `<bot name>` is a local label set in Settings. It is shown only inside BotDesk.
  - It is never sent to Telegram and never changes the bot's real name or username.
  - If no name is set, show `BotDesk - Unnamed bot` **(assumed)**.
- Entries:
  - **Commands**: add, edit, delete commands and edit, reorder their steps.
  - **Live / Unread**: live chat sub-chats and received files. Shows an unread count badge **(assumed)**.
  - **Settings**
  - **Quit**
- Status line shows connection state: `connected`, `reconnecting`, `offline`.

## 5. Settings (structure)

- **Bot name**: local label for the window title.
- **Bot token**: pasted from BotFather. Stored encrypted. Never shown again after saving.
- **Master password**: change the password that unlocks the token **(assumed)**.
- **Messages** (all editable text):
  - "Connecting you with the bot's owner" (sent when a live chat is requested).
  - "Request expired" (sent when a live chat request expires) **(assumed)**.
  - "Chat closed" (sent when the owner closes a live chat) **(assumed)**.
  - "Unknown command" (reply to a `/command` that does not exist).
- **Live chat window**: seconds before an unanswered request expires.
  - Default `600` (10 minutes).
  - Accepts whole numbers from `30` to `86400` **(assumed)**.
  - An invalid value (text, 0, negative, out of range) shows an error message and asks the owner to enter it again. The previous value stays until a valid one is saved.
- **Files folder**: where saved files go **(assumed)**.
- **Sync Telegram command menu**: on/off toggle (see 7.1).

## 6. Commands and actions

A command has a name and an ordered list of steps. Steps run in order.

1. **send message**: sends configured text to the user.
2. **request to live chat**: opens a live chat sub-chat for this user (see 8) and sends the "Connecting you with the bot's owner" message.
3. **wait for file**: the bot waits for the user to send a file. The received file becomes an item in Unread (see 9).
4. **ask a question**: sends the step's text as a question, then waits for the user's next plain-text message. That message is stored as an answer and shown in Unread, with the question. There is no timeout; the wait lasts until the user replies or the command is replaced.
5. **ask with buttons**: the first line of the step text is the question. Each following line is one button label (up to 8, each cut to 64 characters). The buttons are sent as a Telegram reply keyboard. Pressing one stores the label as an answer in Unread. Text that matches no button is ignored and the buttons stay open.
6. **wait for a delay**: pauses the command for a number of seconds, from 1 to 86400. The due time is stored in the database, so the delay survives a restart. When it passes, the remaining steps run.

Commands run their steps in order. An action that waits (wait for file, ask a question, ask with buttons, wait for a delay) pauses the command until its condition is met. Only one wait is active per user at a time; starting a new command with a wait replaces the older one.

### 6.1 Command name rules
- Lowercase letters, digits and underscores, 1 to 32 characters, unique.
- `/start` is a normal, editable command.

### 6.2 Message routing
- A message starting with `/` is always a command, even while a live chat is open. Example: `/help` or a "wait for file" flow works during a chat.
- Unknown `/command`: reply once with the editable "Unknown command" message.
- Plain text from a user with **no open live chat**: ignored and not stored.
- Plain text from a user with an open live chat: stored in that sub-chat.

## 7. Telegram integration

### 7.1 Command menu sync
When commands are saved, BotDesk may update the bot's `/` command menu in Telegram. Controlled by a Settings toggle.

### 7.2 Connection loss
- The bot reconnects automatically.
- The status line shows `connected`, `reconnecting` or `offline`.
- Nothing in the database is lost.
- While BotDesk is closed the bot is offline. Telegram keeps users' messages and the bot picks them up at next start.

## 8. Live chat

A live chat request is a full sub-chat.

- On request: the user receives the editable "connecting you with the bot's owner" message. The owner gets an in-terminal notification and the sub-chat appears in **Live / Unread**.
- Every plain-text message the user sends after the request is stored in the sub-chat.
- When the owner opens it, they see all messages sent since the request.
- The owner's first reply marks the request `accepted`. States: `waiting`, `accepted`, `expired`, `closed`.
- **Expiry:** if the owner does not reply within the live chat window (Settings, default 600 s), the request becomes `expired` and the user gets the "Request expired" message.
- The owner closes a chat with a key. The user gets the "Chat closed" message and the bot returns to normal commands.
- **Restart:** open requests persist in SQLite. At startup, requests whose window has already passed are marked `expired`. The others keep their remaining time.
- **New-item alert:** a new live chat request or file rings the terminal bell and updates the unread badge on the main menu.

## 9. Files ("wait for file")

- A received file never saves itself.
- It appears in **Unread** as an item containing the **file name exactly as the user sent it** and the **caption**.
- For each item the owner chooses **delete** (remove from Unread) or **save** (write to the Files folder).
- **Safe saving:**
  - Sanitize the original file name when writing to disk (strip path separators and characters invalid on Windows).
  - If the name already exists, add a numeric suffix.
  - Telegram's bot download limit is 20 MB. Show this as a known limit. BotDesk does not enforce it.

## 10. Security

- The token is encrypted with a key derived from the master password: **scrypt** for key derivation, **AES-GCM** for encryption.
- The encrypted token is stored in a local file or SQLite row. The master password is never stored.
- The token never appears in logs or on screen after it is saved.
- A forgotten password cannot be recovered. The owner re-enters the token and sets a new password.

## 11. Dependencies

- Python 3.11 or newer **(assumed)**.
- `textual` (TUI).
- `aiogram` (Telegram Bot API).
- `cryptography` (scrypt, AES-GCM).
- SQLite through the standard library `sqlite3`.
- List the packages in a `requirements.txt` file.

## 12. README

Instructions for the AI that builds BotDesk: after the program works, write `README.md` in the project folder. It must describe the program as built, not as planned. Include:

1. What BotDesk is, in two or three sentences.
2. Requirements (Python version, packages) and how to install them.
3. How to get a bot token from BotFather and how to paste it in Settings.
4. First launch: setting the master password, and the warning that a forgotten password cannot be recovered.
5. How to run: `python -m botdesk`.
6. A walkthrough: create a command with the three action types, test it in Telegram, open a live chat from Live / Unread, save or delete a received file.
7. A table of Settings and their defaults, including the 600 s live chat window.
8. Keybindings, copied from the real implementation.
9. Where data, logs and saved files are stored.
10. Known limits: one bot only, the 20 MB Telegram download limit, no password recovery.

Do not document features that are not implemented.
