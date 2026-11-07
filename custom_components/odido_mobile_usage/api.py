"""Browser authentication support for the official app handoff only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from email.utils import formatdate
from html.parser import HTMLParser
from http.cookies import Morsel, SimpleCookie
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from .const import LOGIN_URL
from .cookie_store import valid_cookies


class OdidoError(Exception):
    """Base Odido error."""


class OdidoConnectionError(OdidoError):
    """The Odido website could not be reached."""


class OdidoInvalidAuth(OdidoError):
    """Credentials or verification code were rejected."""


class OdidoChallengeExpired(OdidoInvalidAuth):
    """Restart login instead of resubmitting an accepted verification code."""


class OdidoVerificationRequired(OdidoError):
    """A verification code is required."""


class OdidoParseError(OdidoError):
    """The website response no longer has the expected shape."""


@dataclass(slots=True)
class HtmlForm:
    """A parsed HTML form."""

    action: str = ""
    method: str = "post"
    fields: dict[str, str] | None = None
    password_field: str | None = None
    code_field: str | None = None
    submit_fields: dict[str, str] = field(default_factory=dict)
    code_length: int = 0
    subscription_choices: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.fields is None:
            self.fields = {}


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[HtmlForm] = []
        self._current: HtmlForm | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form":
            self._current = HtmlForm(
                action=attributes.get("action") or "",
                method=(attributes.get("method") or "post").lower(),
            )
            self.forms.append(self._current)
            return
        if (
            tag not in ("input", "button")
            or self._current is None
            or "disabled" in attributes
        ):
            return
        name = attributes.get("name")
        if not name:
            return
        if name.endswith(".ContactDashboard.SwitchMobileSubscription"):
            match = re.fullmatch(
                r"SelectSubscriberByLinkId\(this,\s*'([^']+)'\)\s*;?",
                attributes.get("onclick", ""),
            )
            if match:
                self._current.subscription_choices.append((name, match[1]))
        input_type = (
            attributes.get("type") or ("submit" if tag == "button" else "text")
        ).lower()
        if input_type == "submit":
            self._current.submit_fields[name] = attributes.get("value") or ""
            return
        if input_type in ("submit", "button", "reset", "file"):
            return
        if input_type in ("checkbox", "radio") and "checked" not in attributes:
            return
        self._current.fields[name] = attributes.get("value") or (
            "on" if input_type in ("checkbox", "radio") else ""
        )
        if input_type == "password":
            self._current.password_field = name
        lowered = f"{name} {attributes.get('id', '')} {attributes.get('placeholder', '')}".lower()
        if input_type not in ("hidden", "submit", "button") and any(
            word in lowered
            for word in ("code", "otp", "token", "verification", "verificatie")
        ):
            self._current.code_field = name
            self._current.code_length += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def parse_forms(html: str) -> list[HtmlForm]:
    """Parse forms without adding a third-party HTML dependency."""
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


def _find_login_form(forms: list[HtmlForm]) -> HtmlForm | None:
    return next((form for form in forms if form.password_field), None)


def _find_code_form(forms: list[HtmlForm]) -> HtmlForm | None:
    return next((form for form in forms if form.code_field), None)


def _looks_logged_in(url: str, html: str) -> bool:
    forms = parse_forms(html)
    return (
        URL(url).path.startswith("/my/")
        and _find_login_form(forms) is None
        and _find_code_form(forms) is None
        and 'href="/my/abonnement-overzicht"' in html
    )


class _OdidoCookieJar(CookieJar):
    """Keep Odido's original token encoding when aiohttp builds the header."""

    def _build_morsel(self, cookie):
        morsel = Morsel()
        morsel.set(cookie.key, cookie.value, cookie.coded_value)
        return morsel


class OdidoClient:
    """Use an account-specific jar, never HA's shared session cookies."""

    def __init__(
        self, session, username, password, *, cookies=None, auto_reauth_blocked=False
    ):
        self.username = username
        self._password = password
        self._pending_form = None
        self._pending_url = None
        self._finalize_form = None
        self._authenticated = bool(cookies)
        self.auto_reauth_blocked = auto_reauth_blocked
        self._last_response = None
        self._usage_debug = {}
        self._cookies = _OdidoCookieJar()
        if cookies:
            if not valid_cookies(cookies):
                raise OdidoParseError("Invalid saved cookie format")
            self._restore_cookies(cookies)

    @staticmethod
    def _safe_url(url):
        target = URL(url)
        if (
            target.scheme != "https"
            or target.host != "www.odido.nl"
            or target.port != 443
            or target.user is not None
            or not (target.path == "/login" or target.path.startswith("/my/"))
        ):
            raise OdidoParseError("Unexpected Odido URL")
        return str(target.with_fragment(None))

    async def _request(self, method, url, data=None, *, ajax=False, expect_json=False):
        original_url = url
        self._last_response = None
        try:
            async with ClientSession(
                cookie_jar=self._cookies, timeout=ClientTimeout(total=30)
            ) as session:
                for _ in range(6):
                    url = self._safe_url(url)
                    headers = {"Referer": LOGIN_URL, "Origin": "https://www.odido.nl"}
                    if ajax:
                        headers["X-Requested-With"] = "XMLHttpRequest"
                        headers["Content-Type"] = (
                            "application/x-www-form-urlencoded; charset=UTF-8"
                        )
                    if expect_json:
                        headers["Accept"] = (
                            "application/json, text/javascript, */*; q=0.01"
                        )
                    async with session.request(
                        method, url, data=data, allow_redirects=False, headers=headers
                    ) as response:
                        if response.status in (401, 403):
                            raise OdidoInvalidAuth(
                                "Odido rejected the session or credentials"
                            )
                        if response.status in (301, 302, 303, 307, 308):
                            location = response.headers.get("Location")
                            if not location:
                                raise OdidoConnectionError("Invalid Odido redirect")
                            url = self._safe_url(urljoin(url, location))
                            if response.status in (301, 302, 303):
                                method, data = "GET", None
                            continue
                        if response.status >= 400:
                            raise OdidoConnectionError(f"Odido HTTP {response.status}")
                        body = await response.text()
                        forms = (
                            parse_forms(body) if body.lstrip().startswith("<") else []
                        )
                        self._last_response = {
                            "status": response.status,
                            "format": "html"
                            if body.lstrip().startswith("<")
                            else "json"
                            if body.lstrip("\ufeff \t\r\n").startswith(("{", "["))
                            else "other",
                            "page": "login"
                            if _find_login_form(forms)
                            else "verification"
                            if _find_code_form(forms)
                            else "other",
                            "length": len(body),
                            "redirected": url != original_url,
                        }
                        return url, body
                raise OdidoConnectionError("Too many Odido redirects")
        except (ClientError, TimeoutError, UnicodeError):
            raise OdidoConnectionError("Odido request failed") from None

    async def _get_text(self, url):
        return await self._request("GET", url)

    async def _post_form(self, url, data, *, ajax=False, expect_json=False):
        return await self._request(
            "POST", url, data, ajax=ajax, expect_json=expect_json
        )

    login_url = LOGIN_URL

    def _login_complete(self, url, html):
        return _looks_logged_in(url, html)

    async def async_start_login(self, *, preserve_cookies=False):
        self._pending_form = self._pending_url = None
        self._finalize_form = None
        self._authenticated = False
        if not preserve_cookies:
            self._cookies.clear()
        url, html = await self._get_text(self.login_url)
        if preserve_cookies and self._login_complete(url, html):
            self._authenticated = True
            return False
        form = _find_login_form(parse_forms(html))
        if form is None:
            raise OdidoParseError("Odido login form missing")
        username_field = next(
            (
                name
                for name in form.fields
                if "user" in name.lower() or "email" in name.lower()
            ),
            None,
        )
        if username_field is None or form.method != "post":
            raise OdidoParseError("Odido login fields missing")
        data = dict(form.fields)
        data[username_field] = self.username
        data[form.password_field] = self._password
        for name, value in form.submit_fields.items():
            if name.endswith(".Login.Login"):
                data[name] = value
        url, html = await self._post_form(
            self._safe_url(urljoin(url, form.action)), data
        )
        forms = parse_forms(html)
        code_form = _find_code_form(forms)
        if code_form is not None:
            self._pending_form, self._pending_url = code_form, url
            validation = next(
                (
                    key
                    for key in code_form.fields
                    if key.endswith(".Login.ValidateVerificationCode")
                ),
                None,
            )
            if validation:
                self._finalize_form = next(
                    (
                        f
                        for f in forms
                        if any(key.endswith(".Login.Validate") for key in f.fields)
                    ),
                    None,
                )
                if self._finalize_form is None:
                    raise OdidoParseError(
                        "Odido verification finalization form missing"
                    )
            return True
        if not self._login_complete(url, html):
            raise OdidoInvalidAuth("Odido rejected the login")
        self._authenticated = True
        return False

    async def async_submit_code(self, code):
        form = self._pending_form
        if form is None or form.code_field is None or form.method != "post":
            raise OdidoParseError("No verification challenge pending")
        if not code or len(code) > 32 or not code.isascii() or not code.isalnum():
            raise OdidoInvalidAuth("Invalid verification code")
        data = dict(form.fields)
        data[form.code_field] = code
        if any(key.endswith(".Login.ValidateVerificationCode") for key in form.fields):
            if len(code) != 4 or not code.isascii() or not code.isdigit():
                raise OdidoInvalidAuth("Enter the four-digit SMS code")
            # In the observed request the widget's repeated inputs are empty;
            # it sends the complete code only in a separate pincode field.
            data[form.code_field] = ""
            data["pincode"] = code
            target = self._safe_url(urljoin(self._pending_url, form.action))
            _, body = await self._post_form(target, data, ajax=True, expect_json=True)
            try:
                result = json.loads(body.lstrip("\ufeff"))
            except (ValueError, TypeError):
                summary = self._last_response
                detail = ""
                if summary:
                    detail = f" (HTTP {summary['status']}; format={summary['format']}; page={summary['page']}; redirected={summary['redirected']}; length={summary['length']})"
                raise OdidoParseError(
                    "Invalid Odido verification response" + detail
                ) from None
            if not isinstance(result, dict) or result.get("valid") is not True:
                raise OdidoInvalidAuth("Odido rejected the verification code")
            final = self._finalize_form
            if final is None or final.method != "post":
                raise OdidoParseError("Odido finalization form missing")
            # Do not repeat an already accepted OTP if finalization fails.
            self._pending_form = self._pending_url = self._finalize_form = None
            final_url = self._safe_url(urljoin(target, final.action))
            try:
                data = dict(final.fields)
                # Match the website's "remember this device" checkbox.
                if "IsDeviceRecognitionOpted" in data:
                    data["IsDeviceRecognitionOpted"] = "Yes"
                url, html = await self._post_form(final_url, data)
            except (OdidoConnectionError, OdidoInvalidAuth, OdidoParseError):
                raise OdidoChallengeExpired(
                    "Odido session finalization failed; restart login"
                ) from None
            if not self._login_complete(url, html):
                raise OdidoChallengeExpired(
                    "Odido session finalization failed; restart login"
                )
            self._authenticated = True
            return
        url, html = await self._post_form(
            self._safe_url(urljoin(self._pending_url, form.action)), data
        )
        new_form = _find_code_form(parse_forms(html))
        if new_form:
            self._pending_form, self._pending_url = new_form, url
            raise OdidoInvalidAuth("Odido rejected the verification code")
        if not self._login_complete(url, html):
            self._pending_form = self._pending_url = None
            raise OdidoInvalidAuth("Verification session expired")
        self._pending_form = self._pending_url = None
        self._authenticated = True

    def _restore_cookies(self, payload):
        """Restore exact wire encoding and scope, including session cookies."""
        if payload.get("version") == 2:
            for item in payload.get("items", []):
                domain = item.get("domain", "www.odido.nl").lstrip(".")
                if domain not in ("www.odido.nl", "odido.nl"):
                    continue
                cookie = Morsel()
                cookie.set(item["name"], item["value"], item["coded_value"])
                for key in ("path", "secure", "httponly", "samesite", "expires"):
                    if item.get(key):
                        cookie[key] = item[key]
                if not item.get("host_only", False):
                    cookie["domain"] = domain
                self._cookies.update_cookies(
                    {cookie.key: cookie}, response_url=URL("https://" + domain + "/")
                )
        else:
            # Old versions stored values only. Odido emits raw cookie tokens;
            # SimpleCookie assignment would add quotes to tokens containing '='.
            for key, value in payload.items():
                cookie = SimpleCookie()
                cookie.load(key + "=" + value)
                if key not in cookie or cookie[key].value != value:
                    cookie = SimpleCookie({key: value})
                cookie[key]["path"] = "/"
                self._cookies.update_cookies(cookie, response_url=URL(LOGIN_URL))

    def export_cookies(self):
        if self._pending_form is not None or not self._authenticated:
            return {}
        items = []
        # Iteration removes expired cookies. Convert Max-Age to an absolute
        # expiry so restoring a saved session cannot extend a cookie's lifetime.
        for cookie in self._cookies:
            domain = cookie["domain"].lstrip(".")
            if domain not in ("www.odido.nl", "odido.nl"):
                continue
            item = {
                "name": cookie.key,
                "value": cookie.value,
                "coded_value": cookie.coded_value,
                "domain": domain,
                "host_only": (
                    (domain, cookie.key) in self._cookies._host_only_cookies
                    or (domain, cookie["path"].rstrip("/"), cookie.key)
                    in self._cookies._host_only_cookies
                ),
            }
            for key in ("path", "secure", "httponly", "samesite", "expires"):
                item[key] = cookie[key]
            expiry = self._cookies._expirations.get(
                (cookie["domain"], cookie["path"].rstrip("/"), cookie.key)
            )
            if expiry is not None:
                item["expires"] = formatdate(expiry, usegmt=True)
            items.append(item)
        return {"version": 2, "items": items} if items else {}

    def debug_summary(self):
        return {
            "verification_pending": self._pending_form is not None,
            "usage_structure": self._usage_debug,
            "last_response": self._last_response,
        }
