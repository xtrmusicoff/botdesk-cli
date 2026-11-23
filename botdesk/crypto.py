"""Token encryption: scrypt key derivation + AES-GCM.

The vault is a JSON string holding the salt, nonce and ciphertext.
The master password itself is never stored.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
_AAD = b"botdesk-token-v1"


class WrongPassword(Exception):
    """The master password does not decrypt the vault."""


class Secret:
    """Wraps a sensitive string so it never shows up in reprs or tracebacks."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(password.encode("utf-8"))


def encrypt_token(token: str, password: str) -> str:
    """Encrypt the token (may be empty) and return the vault JSON."""
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    ciphertext = AESGCM(key).encrypt(nonce, token.encode("utf-8"), _AAD)
    return json.dumps(
        {
            "v": 1,
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": _b64(salt),
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }
    )


def decrypt_token(vault: str, password: str) -> str:
    """Decrypt the vault. Raises WrongPassword if the password is wrong."""
    data = json.loads(vault)
    key = _derive(
        password,
        base64.b64decode(data["salt"]),
        int(data["n"]),
        int(data["r"]),
        int(data["p"]),
    )
    try:
        plain = AESGCM(key).decrypt(
            base64.b64decode(data["nonce"]), base64.b64decode(data["ciphertext"]), _AAD
        )
    except InvalidTag:
        raise WrongPassword() from None
    return plain.decode("utf-8")
