"""Odido Android app transport: website bootstrap, Fernet return and CAPI reads.

The public browser return protocol and resource labels come from Android 9.10.0.
Never use the retired direct CAPI username/password login or persist OTPs.
"""

from __future__ import annotations

import base64
import json
import math
from time import time
from urllib.parse import parse_qs, urljoin, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar
from cryptography.fernet import Fernet, InvalidToken
from yarl import URL

from .api import (
    OdidoClient,
    OdidoConnectionError,
    OdidoInvalidAuth,
    OdidoParseError,
    _looks_logged_in,
)
from .app_const import APP_LOGIN_URL, CAPI_BASE, CLIENT_ID, CLIENT_SECRET, RETURN_KEY
from .cookie_store import valid_cookies
from .usage import UsageData, number


def presentation_gb(value):
    """Use provider-presented units instead of guessing binary/decimal counters."""
    import re

    if not isinstance(value, str):
        raise OdidoParseError("App data presentation missing")
    match = re.fullmatch(r"\s*([\d., ]+)\s*(KB|MB|GB)\s*", value, re.IGNORECASE)
    if not match:
        raise OdidoParseError("App data presentation unsupported")
    return (
        float(number(match[1])) / {"KB": 1000000, "MB": 1000, "GB": 1}[match[2].upper()]
    )


def parse_app_usage(data, balance=None, details=None):
    """Normalize observed CAPI buckets; discard all per-call details immediately."""
    if not isinstance(data, dict) or not isinstance(data.get("Bundles"), list):
        raise OdidoParseError("App bundles missing")
    candidates = []
    for bundle in data["Bundles"]:
        if not isinstance(bundle, dict):
            raise OdidoParseError("App bundle response invalid")
        for bucket in bundle.get("Buckets", [bundle]):
            if (
                isinstance(bucket, dict)
                and bucket.get("Unit") in ("KB", "MB", "GB")
                and not bucket.get("DoNotSumInOverview", False)
            ):
                candidates.append(bucket)
    if len(candidates) != 1:
        raise OdidoParseError("Expected one app data bundle")
    bucket = candidates[0]
    limit = presentation_gb(
        bucket.get("LimitValuePresentation", bucket.get("MaxUnitsPresentation"))
    )
    remaining = presentation_gb(
        bucket.get("RemainingValuePresentation", bucket.get("FreeUnitsPresentation"))
    )
    if limit <= 0 or not 0 <= remaining <= limit:
        raise OdidoParseError("App allowance values inconsistent")
    used = limit - remaining
    scale = None

    def counter(value):
        if type(value) not in (int, float) or value < 0 or not math.isfinite(value):
            raise OdidoParseError("App usage counter invalid")
        return value

    if all(k in bucket for k in ("LimitValue", "RemainingValue", "UsedValue")):
        raw_limit, raw_remaining, raw_used = (
            counter(bucket[k]) for k in ("LimitValue", "RemainingValue", "UsedValue")
        )
        if raw_limit <= 0 or abs(raw_used + raw_remaining - raw_limit) > 1:
            raise OdidoParseError("App usage counters inconsistent")
        # Calibrate the raw counter against the provider's explicit limit label:
        # observed 10,240,000 KB = 10,000 MB = 10 GB, avoiding unit guesses.
        scale = limit / raw_limit
        used, remaining = raw_used * scale, raw_remaining * scale
    balance = balance if isinstance(balance, dict) else data
    extra = (
        0.0
        if type(balance.get("UnbilledAirTimeIncludingVat")) in (int, float)
        and balance["UnbilledAirTimeIncludingVat"] == 0
        else None
    )
    voice = (
        "Onbeperkt"
        if any(
            isinstance(b, dict)
            and b.get("IsUnlimitedCalling")
            in (True, "NL", "EU", "NLEU", "EUUSA", "EUNONL", "TMOUSA", "TM-USA")
            for b in balance.get("Bundles", [])
        )
        else None
    )
    voice_used = sms_used = reported_data = None
    if details is not None:
        if not isinstance(details, dict) or not isinstance(
            details.get("Breakdowns"), list
        ):
            raise OdidoParseError("App usage breakdowns missing")
        seconds = sms = volume = 0
        has_data = False
        for group in details["Breakdowns"]:
            if not isinstance(group, dict) or not isinstance(
                group.get("CallDetailsRecords"), list
            ):
                raise OdidoParseError("App usage records invalid")
            for record in group["CallDetailsRecords"]:
                if not isinstance(record, dict):
                    raise OdidoParseError("App usage record invalid")
                value = counter(record.get("RatedVolume"))
                unit = record.get("RatedVolumeUnit")
                if unit == "Sec":
                    seconds += value
                elif unit == "SMS":
                    if int(value) != value:
                        raise OdidoParseError("App SMS count invalid")
                    sms += int(value)
                elif unit == bucket["Unit"]:
                    volume += value
                    has_data = True
                else:
                    raise OdidoParseError("App usage unit unsupported")
        voice_used, sms_used = seconds / 60, sms
        if has_data and scale is not None:
            reported_data = volume * scale
    return UsageData(
        used_gb=round(used, 6),
        remaining_gb=round(remaining, 6),
        limit_gb=limit,
        percentage=round(used / limit * 100, 4),
        voice_allowance=voice,
        voice_used_minutes=voice_used,
        sms_used=sms_used,
        reported_data_used_gb=reported_data,
        extra_costs=extra,
    )


class OdidoAppClient(OdidoClient):
    """Keep browser bootstrap cookies separate from persisted app credentials."""

    login_url = APP_LOGIN_URL

    def __init__(
        self, session, username, password, *, app_session=None, bootstrap=None, **kwargs
    ):
        self._login_context_token = (
            Fernet(RETURN_KEY.encode()).encrypt(CLIENT_ID.encode()).decode()
        )
        self.login_url = str(
            URL(APP_LOGIN_URL).update_query(token=self._login_context_token)
        )
        # Website cookies from another transport must not become app auth.
        kwargs.pop("cookies", None)
        super().__init__(session, username, password, **kwargs)
        self._app_code = None
        self._app_token = None
        self._app_debug = {}
        self._bootstrap_verified = False
        if bootstrap:
            if not valid_cookies(bootstrap):
                raise OdidoParseError("Invalid saved browser session")
            self._restore_cookies(bootstrap)
        if app_session:
            if (
                not isinstance(app_session, dict)
                or app_session.get("version") != 1
                or not isinstance(app_session.get("token"), str)
                or not app_session["token"]
            ):
                raise OdidoParseError("Invalid app session format")
            self._app_token = app_session["token"]

    def _safe_url(self, url):
        target = URL(url)
        if (
            target.scheme == "https"
            and target.host == "www.odido.nl"
            and target.port == 443
            and target.user is None
            and target.path.rstrip("/").lower() == "/loginappresult"
        ):
            return str(target.with_fragment(None))
        safe = URL(OdidoClient._safe_url(url))
        if safe.path == "/login":
            # SMS forms may use action="/login" without the app-return query.
            # Keep the requested destination on validation and finalization too.
            safe = safe.update_query(returnSystem="app", nav="off")
            safe = safe.update_query(token=self._login_context_token)
        return str(safe)

    def _login_complete(self, url, html):
        target = urlsplit(url)
        if _looks_logged_in(url, html):
            self._bootstrap_verified = True
        self._app_debug["completion_page"] = self._page_kind(url)
        if target.path.rstrip("/").lower() != "/loginappresult":
            return False
        self._app_debug["stage"] = "browser_return"
        encrypted = parse_qs(target.query).get("token", [])
        if len(encrypted) != 1 or len(encrypted[0]) > 32768:
            raise OdidoParseError("App return token missing")
        try:
            cipher = Fernet(RETURN_KEY.encode())
            result = json.loads(cipher.decrypt(encrypted[0].encode(), ttl=600))
            if (
                not isinstance(result, dict)
                or type(result.get("ResultCode")) is not int
                or result.get("ResultCode") != 0
            ):
                raise OdidoInvalidAuth("App browser login rejected")
            if (
                not isinstance(result.get("sub"), str)
                or result["sub"].casefold() != self.username.casefold()
            ):
                raise OdidoInvalidAuth("App return account mismatch")
            now = time()
            if not all(type(result.get(key)) is int for key in ("exp", "nbf", "iat")):
                raise ValueError
            if result["exp"] <= now or result["nbf"] > now + 30:
                raise ValueError
            inner = result.get("AccessToken")
            if not isinstance(inner, str):
                raise TypeError
            code = cipher.decrypt(inner.encode(), ttl=600).decode()
            if not code or len(code) > 16384 or any(c in code for c in "\r\n"):
                raise ValueError
        except (InvalidToken, ValueError, TypeError, UnicodeError):
            raise OdidoParseError("Invalid encrypted app return") from None
        self._app_code = code
        self._bootstrap_verified = True
        return True

    @staticmethod
    def _page_kind(url):
        path = urlsplit(url).path.rstrip("/").lower()
        return {
            "/login": "login",
            "/loginappresult": "app_return",
            "/my/abonnement-overzicht": "account_overview",
        }.get(path, "other")

    async def _post_form(self, url, data, **kwargs):
        self._app_debug["browser_stage"] = (
            "otp_validation"
            if any(k.endswith(".Login.ValidateVerificationCode") for k in data)
            else "finalization"
            if any(k.endswith(".Login.Validate") for k in data)
            else "login"
        )
        result = await super()._post_form(url, data, **kwargs)
        self._app_debug["response_page"] = self._page_kind(result[0])
        return result

    async def async_resume_bootstrap(self):
        """Retry the app handoff with cookies, without submitting credentials/OTP."""
        self._bootstrap_verified = False
        url, html = await self._get_text(self.login_url)
        if not self._login_complete(url, html):
            if self._bootstrap_verified:
                raise OdidoParseError("Authenticated browser did not return to app")
            raise OdidoInvalidAuth("Saved browser session requires sign-in")
        await self._exchange_code()

    def export_bootstrap(self):
        """Only retain cookies after a verifiably authenticated account page."""
        if not self._bootstrap_verified or self._pending_form is not None:
            return {}
        authenticated = self._authenticated
        try:
            self._authenticated = True
            return super().export_cookies()
        finally:
            self._authenticated = authenticated

    async def async_start_login(self, **kwargs):
        # Lnm1.F in Android 9.10.0 encrypts oauth_clientkey with the app's
        # public Fernet key and appends it to the browser login URL.
        self._login_context_token = (
            Fernet(RETURN_KEY.encode()).encrypt(CLIENT_ID.encode()).decode()
        )
        self.login_url = str(
            URL(APP_LOGIN_URL).update_query(token=self._login_context_token)
        )
        self._app_code = self._app_token = None
        pending = await super().async_start_login(**kwargs)
        if not pending:
            await self._exchange_code()
        return pending

    async def async_submit_code(self, code):
        await super().async_submit_code(code)
        await self._exchange_code()

    async def _exchange_code(self):
        if not self._app_code:
            raise OdidoParseError("App authorization code missing")
        code, self._app_code = self._app_code, None
        # Current browser sign-in returns an app access token. Validate it on
        # the read-only account endpoint before retaining it. Older returns can
        # contain an authorization code instead, which must be exchanged once.
        self._app_token = code
        try:
            account = await self._capi("GET", "account/current", "Account", 1)
        except OdidoInvalidAuth:
            self._app_token = None
        except Exception:
            self._app_token = None
            raise
        else:
            if not isinstance(account.get("Resources"), list):
                self._app_token = None
                raise OdidoParseError("App account resource structure missing")
            self.auto_reauth_blocked = False
            return
        result = await self._capi(
            "POST",
            "createtoken",
            "CreateToken",
            1,
            payload={"AuthorizationCode": code},
            basic=True,
        )
        token = result.get("AccessToken")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 16384
            or any(c in token for c in "\r\n")
        ):
            raise OdidoParseError("App access token missing")
        self._app_token = token
        self.auto_reauth_blocked = False

    @staticmethod
    def _capi_url(url):
        target = URL(urljoin(CAPI_BASE, url))
        if (
            target.scheme != "https"
            or target.host != "capi.odido.nl"
            or target.port != 443
            or target.user is not None
            or target.fragment
        ):
            raise OdidoParseError("Unexpected app resource URL")
        return str(target)

    async def _capi(self, method, url, label, version, *, payload=None, basic=False):
        target = self._capi_url(url)
        media = f"application/vnd.capi.tmobile.nl.{label.lower()}.v{version}+json"
        headers = {
            "Accept": "application/json," + media,
            "User-Agent": "Odido/9.10.0 (Android)",
        }
        if basic:
            headers["Authorization"] = (
                "Basic "
                + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            )
            headers["grant_type"] = "authorization_code"
        elif self._app_token:
            headers["Authorization"] = "Bearer " + self._app_token
        else:
            raise OdidoInvalidAuth("App sign-in required")
        self._app_debug = {"stage": label}
        try:
            async with ClientSession(
                cookie_jar=DummyCookieJar(), timeout=ClientTimeout(total=30)
            ) as client:
                for attempt in range(5):
                    async with client.request(
                        method,
                        target,
                        json=payload,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        self._app_debug["status"] = response.status
                        if (
                            response.status in (301, 302, 303, 307, 308)
                            and method == "GET"
                        ):
                            location = response.headers.get("Location")
                            if not location:
                                raise OdidoParseError("App redirect target missing")
                            target = self._capi_url(urljoin(target, location))
                            self._app_debug["redirects"] = attempt + 1
                            continue
                        if response.status == 401:
                            self._app_token = None
                            raise OdidoInvalidAuth("App session rejected")
                        if response.status == 403:
                            raise OdidoParseError("App resource access denied")
                        if response.status >= 500 or response.status == 429:
                            raise OdidoConnectionError(
                                "App service unavailable or rate limited"
                            )
                        if response.status >= 300:
                            raise OdidoParseError("App resource request rejected")
                        if label == "CreateToken" and basic:
                            # Android Lw51.apply reads these values from HTTP headers.
                            # A successful 202 can have an empty or array-shaped body.
                            token = response.headers.get("AccessToken")
                            error = response.headers.get("ErrorCode")
                            self._app_debug["has_access_token_header"] = bool(token)
                            if error not in (None, "", "0"):
                                raise OdidoInvalidAuth("App token exchange rejected")
                            if token:
                                return {"AccessToken": token}
                        try:
                            data = await response.json(content_type=None)
                        except (ValueError, UnicodeError):
                            raise OdidoParseError("App returned no JSON") from None
                        break
                else:
                    raise OdidoParseError("Too many app redirects")
        except (ClientError, TimeoutError):
            raise OdidoConnectionError("App request failed") from None
        if not isinstance(data, dict):
            raise OdidoParseError("Invalid app response structure")
        # Safe structural metadata only, not URLs, account IDs or raw responses.
        self._app_debug["fields"] = sorted(
            k for k in data if isinstance(k, str) and k.isalpha() and len(k) < 64
        )
        if data.get("ErrorCode") or data.get("ApiErrorCode"):
            raise OdidoParseError("App returned an API error")
        return data

    @staticmethod
    def _resources(data, label):
        found = []

        def walk(value, depth=0):
            if depth > 6:
                return
            if isinstance(value, dict):
                resources = value.get("Resources", [])
                if isinstance(resources, list):
                    for resource in resources:
                        if (
                            isinstance(resource, dict)
                            and resource.get("Label") == label
                            and resource.get("Availability") == "Available"
                            and isinstance(resource.get("Url"), str)
                        ):
                            found.append(resource["Url"])
                for key, child in value.items():
                    if key != "Resources" and isinstance(child, (dict, list)):
                        walk(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth + 1)

        walk(data)
        return list(dict.fromkeys(found))

    async def async_get_usage(self):
        if not self._app_token:
            raise OdidoInvalidAuth("App sign-in required")
        account = await self._capi(
            "GET", "account/current?resourcelabel=LinkedSubscriptions", "Account", 1
        )
        linked_urls = self._resources(account, "LinkedSubscriptions")
        if len(linked_urls) == 1:
            linked = await self._capi("GET", linked_urls[0], "LinkedSubscriptions", 1)
            records = linked.get("subscriptions")
            if not isinstance(records, list):
                raise OdidoParseError("App linked subscriptions missing")
            records = [
                r
                for r in records
                if isinstance(r, dict)
                and r.get("SubscriptionType") == "Mobile"
                and r.get("Status") == "Active"
            ]
            if len(records) != 1 or not isinstance(
                records[0].get("SubscriptionURL"), str
            ):
                raise OdidoParseError("Expected exactly one app mobile subscription")
            url = self._capi_url(records[0]["SubscriptionURL"])
            url = str(URL(url).update_query(resourcelabel="PostpaidBundleStatus"))
            subscription = await self._capi("GET", url, "Subscription", 2)
        else:
            # Older single-subscription responses expose Subscriber directly.
            urls = self._resources(account, "Subscriber")
            if len(urls) != 1:
                raise OdidoParseError("Expected exactly one app mobile subscription")
            subscription = await self._capi("GET", urls[0], "Subscriber", 2)

        async def read(label, version):
            urls = self._resources(subscription, label)
            if len(urls) != 1:
                raise OdidoParseError("App allowance resource missing or ambiguous")
            return await self._capi("GET", urls[0], label, version)

        status = await read("PostpaidBundleStatus", 5)
        if not self._resources(subscription, "PostpaidDataBundleStatus"):
            return parse_app_usage(status)
        data = await read("PostpaidDataBundleStatus", 9)
        details = await read("PostpaidBundleStatusDetails", 4)
        return parse_app_usage(data, status, details)

    def export_session(self):
        return {"version": 1, "token": self._app_token} if self._app_token else {}

    def export_cookies(self):
        return {}

    def debug_summary(self):
        return {
            "transport": "app",
            "verification_pending": self._pending_form is not None,
            "has_app_session": bool(self._app_token),
            "app_response": self._app_debug,
            "browser_response": self._last_response,
        }
