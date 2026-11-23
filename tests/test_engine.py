import pytest

from botdesk import store as st
from botdesk.engine import CONNECTED, Engine, Incoming, IncomingFile, parse_command, split_text
from botdesk.store import Store


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "t.db")
    clock = Clock()
    alerts = []
    engine = Engine(store, clock=clock, on_alert=alerts.append)
    yield store, engine, clock, alerts
    store.close()


def outbox(store):
    items = store.pending_outbox(1000)
    for item in items:
        store.delete_outbox(item.id)
    return [(i.chat_id, i.text) for i in items]


def text(chat_id, value):
    return Incoming(chat_id=chat_id, user_label=f"user{chat_id}", text=value)


def file_msg(chat_id, name, caption=None):
    return Incoming(
        chat_id=chat_id, user_label=f"user{chat_id}", caption=caption,
        file=IncomingFile("FILEID", name, 1234),
    )


def make_command(store, name, steps):
    cid = store.add_command(name)
    for action, body in steps:
        store.add_step(cid, action, body)
    return cid


def test_parse_command():
    assert parse_command("/start", None) == "start"
    assert parse_command("/Start arg", None) == "start"
    assert parse_command("/help@MyBot", "mybot") == "help"
    assert parse_command("/help@OtherBot", "mybot") is None
    assert parse_command("/", None) == ""
    assert parse_command("/ hi", None) == ""


def test_split_text():
    assert split_text("abc") == ["abc"]
    assert [len(p) for p in split_text("x" * 5000)] == [4096, 904]


def test_unknown_command_replies_once(env):
    store, engine, *_ = env
    engine.handle(text(1, "/nope"))
    assert outbox(store) == [(1, store.get("msg_unknown"))]


def test_plain_text_without_chat_is_ignored(env):
    store, engine, *_ = env
    engine.handle(text(1, "hello"))
    assert outbox(store) == []
    assert store.list_chats() == []


def test_full_flow(env):
    store, engine, clock, alerts = env
    make_command(store, "support", [
        (st.SEND_MESSAGE, "Hi!"),
        (st.LIVE_CHAT, ""),
        (st.WAIT_FILE, ""),
        (st.SEND_MESSAGE, "Thanks for the file."),
    ])
    engine.handle(text(7, "/support"))
    assert outbox(store) == [(7, "Hi!"), (7, store.get("msg_connecting"))]
    [chat] = store.list_chats()
    assert chat.state == st.WAITING and chat.unread
    assert chat.expires_at == clock.now + 600
    assert len(alerts) == 1

    engine.handle(text(7, "are you there?"))
    assert [m.text for m in store.chat_messages(chat.id)] == ["are you there?"]

    # A /command works during the live chat and does not cancel the file wait.
    make_command(store, "help", [(st.SEND_MESSAGE, "Help text")])
    engine.handle(text(7, "/help"))
    assert outbox(store) == [(7, "Help text")]
    assert [m.text for m in store.chat_messages(chat.id)] == ["are you there?"]

    engine.handle(file_msg(7, "my report.pdf", caption="Q3"))
    [item] = store.list_files()
    assert (item.file_name, item.caption) == ("my report.pdf", "Q3")
    assert outbox(store) == [(7, "Thanks for the file.")]
    assert len(alerts) == 2
    assert store.unread_count() == 2  # unread chat + file


def test_file_without_wait_is_ignored(env):
    store, engine, *_ = env
    engine.handle(file_msg(1, "x.txt"))
    assert store.list_files() == []


def test_owner_reply_accepts_then_close(env):
    store, engine, clock, _ = env
    make_command(store, "talk", [(st.LIVE_CHAT, "")])
    engine.handle(text(5, "/talk"))
    outbox(store)
    chat = store.list_chats()[0]
    engine.owner_reply(chat.id, "Hello, how can I help?")
    assert store.get_chat(chat.id).state == st.ACCEPTED
    assert outbox(store) == [(5, "Hello, how can I help?")]
    clock.now += 10_000
    assert engine.expire_due() == 0  # accepted chats do not expire
    engine.close_chat(chat.id)
    assert store.get_chat(chat.id).state == st.CLOSED
    assert outbox(store) == [(5, store.get("msg_closed"))]
    engine.handle(text(5, "still there?"))
    assert len(store.chat_messages(chat.id)) == 1  # only the owner's reply
    with pytest.raises(ValueError):
        engine.owner_reply(chat.id, "late")


def test_expiry(env):
    store, engine, clock, _ = env
    store.set("live_window", "60")
    make_command(store, "talk", [(st.LIVE_CHAT, "")])
    engine.handle(text(5, "/talk"))
    outbox(store)
    clock.now += 59
    assert engine.expire_due() == 0
    clock.now += 1
    assert engine.expire_due() == 1
    assert store.list_chats()[0].state == st.EXPIRED
    assert outbox(store) == [(5, store.get("msg_expired"))]


def test_no_duplicate_open_chat(env):
    store, engine, *_ = env
    make_command(store, "talk", [(st.LIVE_CHAT, "")])
    engine.handle(text(5, "/talk"))
    engine.handle(text(5, "/talk"))
    assert len(store.list_chats()) == 1


def test_restart_expires_passed_and_keeps_others(tmp_path):
    path = tmp_path / "r.db"
    clock = Clock()
    s1 = Store(path)
    s1.set("live_window", "100")
    e1 = Engine(s1, clock=clock)
    make_command(s1, "talk", [(st.LIVE_CHAT, "")])
    e1.handle(text(1, "/talk"))
    clock.now += 80
    e1.handle(text(2, "/talk"))
    s1.close()

    clock.now += 30  # while closed: chat 1 passed its window, chat 2 has 70 s left
    s2 = Store(path)
    e2 = Engine(s2, clock=clock)
    assert e2.expire_due() == 1
    assert {c.chat_id: c.state for c in s2.list_chats()} == {1: st.EXPIRED, 2: st.WAITING}
    clock.now += 69
    assert e2.expire_due() == 0
    clock.now += 1
    assert e2.expire_due() == 1
    s2.close()


def test_new_wait_replaces_pending_wait(env):
    store, engine, *_ = env
    make_command(store, "a", [(st.WAIT_FILE, ""), (st.SEND_MESSAGE, "from a")])
    make_command(store, "b", [(st.WAIT_FILE, ""), (st.SEND_MESSAGE, "from b")])
    engine.handle(text(1, "/a"))
    engine.handle(text(1, "/b"))
    engine.handle(file_msg(1, "f.bin"))
    assert outbox(store) == [(1, "from b")]


def test_move_step(env):
    store, *_ = env
    cid = make_command(store, "m", [(st.SEND_MESSAGE, "1"), (st.SEND_MESSAGE, "2"), (st.SEND_MESSAGE, "3")])
    third = store.list_steps(cid)[2]
    assert store.move_step(third.id, -1)
    assert [s.text for s in store.list_steps(cid)] == ["1", "3", "2"]
    assert not store.move_step(store.list_steps(cid)[0].id, -1)


def test_deleting_command_clears_pending_wait(env):
    store, engine, *_ = env
    cid = make_command(store, "up", [(st.WAIT_FILE, "")])
    engine.handle(text(1, "/up"))
    store.delete_command(cid)
    engine.handle(file_msg(1, "f.bin"))
    assert store.list_files() == []


class FakeBot:
    async def download(self, file_id, destination, timeout):
        destination.write(b"DATA")


async def test_save_file_sanitizes_and_suffixes(env, tmp_path):
    store, engine, *_ = env
    make_command(store, "up", [(st.WAIT_FILE, "")])
    for _ in range(2):
        engine.handle(text(1, "/up"))
        engine.handle(file_msg(1, "../evil:name.txt"))
    engine.bot = FakeBot()
    engine.status = CONNECTED
    folder = tmp_path / "files"
    items = sorted(store.list_files(), key=lambda i: i.id)
    p1 = await engine.save_file(items[0].id, folder)
    p2 = await engine.save_file(items[1].id, folder)
    assert p1.name == "..evilname.txt" and p2.name == "..evilname_1.txt"
    assert p1.parent == folder and p1.read_bytes() == b"DATA"
    assert store.list_files() == []


async def test_save_file_offline_keeps_item(env, tmp_path):
    store, engine, *_ = env
    make_command(store, "up", [(st.WAIT_FILE, "")])
    engine.handle(text(1, "/up"))
    engine.handle(file_msg(1, "a.txt"))
    item = store.list_files()[0]
    with pytest.raises(RuntimeError):
        await engine.save_file(item.id, tmp_path)
    assert store.get_file(item.id) is not None
