"""Validate the JSON cookie format, including the legacy value-only format."""


def valid_cookies(payload):
    if not isinstance(payload, dict) or not payload:
        return False
    if payload.get("version") != 2:
        return all(
            isinstance(k, str) and isinstance(v, str) for k, v in payload.items()
        )
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        if not all(
            isinstance(item.get(k), str)
            for k in ("name", "value", "coded_value", "domain", "path")
        ):
            return False
        if item["domain"].lstrip(".") not in ("www.odido.nl", "odido.nl"):
            return False
        if not item["path"].startswith("/"):
            return False
        if any(
            not isinstance(item.get(k, ""), (str, bool))
            for k in ("expires", "secure", "httponly", "samesite")
        ):
            return False
    return True
