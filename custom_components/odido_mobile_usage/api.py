"""Small, traceable client for the Mijn Odido website.

Odido does not publish a consumer usage API. This client intentionally models
the website forms instead of relying on browser automation. Keep all website
specific behavior in this module so changes can be debugged safely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from .const import DEFAULT_USAGE_URL, LOGIN_URL
from .website import UsageParseError, parse_usage


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


class OdidoClient:
    """Use an account-specific jar, never HA's shared session cookies."""

    def __init__(
        self, session, username, password, *, usage_url=DEFAULT_USAGE_URL, cookies=None
    ):
        self.username = username
        self._password = password
        self.usage_url = self._safe_url(usage_url)
        self._pending_form = None
        self._pending_url = None
        self._finalize_form = None
        self._authenticated = bool(cookies)
        self._last_response = None
        self._usage_debug = {}
        self._cookies = CookieJar()
        if cookies:
            self._cookies.update_cookies(cookies, response_url=URL(self.usage_url))

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
                        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
                    if expect_json:
                        headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
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
                        forms = parse_forms(body) if body.lstrip().startswith("<") else []
                        self._last_response = {
                            "status": response.status,
                            "format": "html" if body.lstrip().startswith("<") else "json" if body.lstrip("\ufeff \t\r\n").startswith(("{", "[")) else "other",
                            "page": "login" if _find_login_form(forms) else "verification" if _find_code_form(forms) else "other",
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
        return await self._request("POST", url, data, ajax=ajax, expect_json=expect_json)

    async def async_start_login(self):
        self._pending_form = self._pending_url = None
        self._finalize_form = None
        self._authenticated = False
        self._cookies.clear()
        url, html = await self._get_text(LOGIN_URL)
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
        if not _looks_logged_in(url, html):
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
                raise OdidoParseError("Invalid Odido verification response" + detail) from None
            if not isinstance(result, dict) or result.get("valid") is not True:
                raise OdidoInvalidAuth("Odido rejected the verification code")
            final = self._finalize_form
            if final is None or final.method != "post":
                raise OdidoParseError("Odido finalization form missing")
            # Do not repeat an already accepted OTP if finalization fails.
            self._pending_form = self._pending_url = self._finalize_form = None
            final_url = self._safe_url(urljoin(target, final.action))
            try:
                url, html = await self._post_form(final_url, dict(final.fields))
            except (OdidoConnectionError, OdidoInvalidAuth, OdidoParseError):
                raise OdidoChallengeExpired(
                    "Odido session finalization failed; restart login"
                ) from None
            if not _looks_logged_in(url, html):
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
        if not _looks_logged_in(url, html):
            self._pending_form = self._pending_url = None
            raise OdidoInvalidAuth("Verification session expired")
        self._pending_form = self._pending_url = None
        self._authenticated = True

    async def async_get_usage(self):
        if self._pending_form is not None:
            raise OdidoInvalidAuth("Verification is not complete")
        pages = []
        for url in (self.usage_url, "https://www.odido.nl/my/verbruik-en-kosten"):
            result_url, html = await self._get_text(url)
            if URL(result_url).path == "/my/abonnement-overzicht":
                await self._select_only_subscription(result_url, html)
                result_url, html = await self._get_text(url)
                if URL(result_url).path == "/my/abonnement-overzicht":
                    raise OdidoParseError(
                        "Odido did not select the mobile subscription"
                    )
            if not _looks_logged_in(result_url, html):
                raise OdidoInvalidAuth("Odido session expired")
            html = await self._load_usage_sections(url, html)
            pages.append(html)
        try:
            return parse_usage(*pages)
        except UsageParseError as err:
            raise OdidoParseError(
                f"Odido usage sections incomplete: {err}"
            ) from None

    async def _load_usage_sections(self, url, html):
        """Load the explicit read-only forms used by the site's usage widgets.

        Never replay arbitrary buttons, invoice graphs or the Refresh action.
        """
        path = URL(url).path
        if path not in ("/my/tegoed", "/my/verbruik-en-kosten"):
            raise OdidoParseError("Unexpected usage page")
        suffix = ".BundleUsage.GetStatus" if path == "/my/tegoed" else ".SimUsageDetail.GetUsageTotals"
        forms = [form for form in parse_forms(html) if form.method == "post"
                 and any(key.endswith(suffix) for key in form.fields)
                 and URL(self._safe_url(urljoin(url, form.action))).path == path]
        page = "allowance" if path == "/my/tegoed" else "details"
        self._usage_debug[page] = {"read_forms": len(forms), "loaded_sections": 0}
        if not forms:
            return html
        if len(forms) > 4:
            raise OdidoParseError("Unexpected number of usage initialization forms")
        fragments = []
        for form in forms:
            _, fragment = await self._post_form(self._safe_url(urljoin(url, form.action)), dict(form.fields), ajax=True)
            parsed = parse_forms(fragment)
            if _find_login_form(parsed) or _find_code_form(parsed):
                raise OdidoInvalidAuth("Odido usage session expired")
            if not fragment.lstrip().startswith("<"):
                raise OdidoParseError("Usage initialization returned no HTML")
            fragments.append(fragment)
            self._usage_debug[page]["loaded_sections"] += 1
        return "<main>" + "\n".join(fragments) + "</main>"

    async def _select_only_subscription(self, url, html):
        """Follow the account page's own selector; never guess among accounts."""
        if not _looks_logged_in(url, html):
            raise OdidoInvalidAuth("Odido session expired")
        forms = parse_forms(html)
        initial = next(
            (
                f
                for f in forms
                if any(k.endswith(".ContactDashboard.Initial") for k in f.fields)
            ),
            None,
        )
        if initial is not None:
            if initial.method != "post":
                raise OdidoParseError("Unexpected subscription form")
            _, html = await self._post_form(
                self._safe_url(urljoin(url, initial.action)),
                dict(initial.fields),
                ajax=True,
            )
            forms = parse_forms(html)
        choices = [
            (f, name, link_id)
            for f in forms
            for name, link_id in f.subscription_choices
        ]
        if len(choices) != 1:
            raise OdidoParseError("Expected exactly one mobile subscription")
        form, button, link_id = choices[0]
        selected = next(
            (
                key
                for key in form.fields
                if key.endswith(".ContactDashboard.SelectedSubscriber")
            ),
            None,
        )
        if selected is None or form.method != "post":
            raise OdidoParseError("Subscription selection field missing")
        data = {**form.fields, selected: link_id, button: ""}
        await self._post_form(
            self._safe_url(urljoin(url, form.action)), data, ajax=True
        )

    def export_cookies(self):
        if self._pending_form is not None or not self._authenticated:
            return {}
        return {
            key: value.value
            for key, value in self._cookies.filter_cookies(URL(self.usage_url)).items()
        }

    def debug_summary(self):
        return {"verification_pending": self._pending_form is not None, "usage_structure": self._usage_debug}
