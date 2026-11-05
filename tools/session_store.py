"""Local session cache; never save passwords, verification codes or page content."""
from custom_components.odido_mobile_usage.cookie_store import valid_cookies
import hashlib
import json
import os
from pathlib import Path
import tempfile


def account_key(username):
    return hashlib.sha256(username.strip().lower().encode()).hexdigest()


def load_session(path, username):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("account") != account_key(username):
        return None
    cookies = data.get("cookies")
    if not valid_cookies(cookies):
        return None
    return cookies


def save_session(path, username, cookies):
    if not cookies:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"account": account_key(username), "cookies": cookies}, stream)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clear_session(path):
    Path(path).unlink(missing_ok=True)
