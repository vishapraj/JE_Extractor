"""HTTP Basic auth for the page: any user name, the APP_PASSWORD password."""

import base64
import binascii
import secrets


def password_ok(header: str, password: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        given = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
    except (binascii.Error, UnicodeDecodeError):
        return False
    return secrets.compare_digest(given.encode(), password.encode())
