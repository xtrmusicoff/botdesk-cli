import json

import pytest

from botdesk.crypto import Secret, WrongPassword, decrypt_token, encrypt_token
from botdesk.logsetup import redact, register_secret
from botdesk.validation import (
    command_name_error,
    parse_window,
    reserve_unique_file,
    sanitize_filename,
)

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw0"


def test_token_roundtrip_and_no_plaintext():
    vault = encrypt_token(TOKEN, "correct horse")
    assert TOKEN not in vault
    assert json.loads(vault)["kdf"] == "scrypt"
    assert decrypt_token(vault, "correct horse") == TOKEN


def test_wrong_password():
    vault = encrypt_token(TOKEN, "correct horse")
    with pytest.raises(WrongPassword):
        decrypt_token(vault, "wrong horse")


def test_empty_token_vault_verifies_password():
    assert decrypt_token(encrypt_token("", "password1"), "password1") == ""


def test_secret_repr_hides_value():
    assert TOKEN not in repr(Secret(TOKEN))
    assert TOKEN not in str(Secret(TOKEN))


def test_redact_token():
    assert TOKEN not in redact(f"GET https://api.telegram.org/bot{TOKEN}/getMe failed")
    register_secret("short:secret")
    assert "short:secret" not in redact("x short:secret y")


@pytest.mark.parametrize("name", ["start", "a", "help_2", "x" * 32])
def test_valid_command_names(name):
    assert command_name_error(name, set()) is None


@pytest.mark.parametrize("name", ["", "Start", "with space", "x" * 33, "dash-name", "\u00fcn\u00ef"])
def test_invalid_command_names(name):
    assert command_name_error(name, set()) is not None


def test_duplicate_command_name():
    assert command_name_error("start", {"start"}) is not None


@pytest.mark.parametrize("text,value", [("600", 600), (" 30 ", 30), ("86400", 86400)])
def test_window_valid(text, value):
    assert parse_window(text) == value


@pytest.mark.parametrize("text", ["", "abc", "0", "-5", "29", "86401", "10.5", "+60", "\u0666\u0660\u0660"])
def test_window_invalid(text):
    with pytest.raises(ValueError):
        parse_window(text)


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "....etcpasswd"),
        ("..\\..\\win.ini", "....win.ini"),
        ('a<b>c:d"e|f?g*.txt', "abcdefg.txt"),
        ("CON.txt", "_CON.txt"),
        ("nul", "_nul"),
        ("trailing. . ", "trailing"),
        ("..", "file"),
        ("", "file"),
        ("tab\there.txt", "tabhere.txt"),
    ],
)
def test_sanitize(raw, clean):
    assert sanitize_filename(raw) == clean


def test_sanitize_never_contains_separators():
    for raw in ["../../x", "a/b\\c", "C:\\Windows\\system32\\x.dll"]:
        clean = sanitize_filename(raw)
        assert "/" not in clean and "\\" not in clean and ":" not in clean


def test_sanitize_long_name_keeps_suffix():
    name = sanitize_filename("a" * 500 + ".pdf")
    assert len(name) <= 150 and name.endswith(".pdf")


def test_reserve_unique_adds_suffix(tmp_path):
    (tmp_path / "doc.txt").write_text("old")
    path1, h1 = reserve_unique_file(tmp_path, "doc.txt")
    h1.close()
    path2, h2 = reserve_unique_file(tmp_path, "doc.txt")
    h2.close()
    assert path1.name == "doc_1.txt"
    assert path2.name == "doc_2.txt"
    assert (tmp_path / "doc.txt").read_text() == "old"
