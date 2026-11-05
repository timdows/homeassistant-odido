"""App auth bootstrap and read-only CAPI contracts, using synthetic tokens."""

import json
import unittest
from pathlib import Path
from time import time
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.fernet import Fernet
from yarl import URL

from custom_components.odido_mobile_usage.app_api import (
    OdidoAppClient,
    OdidoInvalidAuth,
    OdidoParseError,
    parse_app_usage,
)
from custom_components.odido_mobile_usage.app_const import CLIENT_ID, RETURN_KEY

USER = "test@example.invalid"
STATUS = {
    "Bundles": [
        {
            "Unit": "MB",
            "MaxUnitsPresentation": "10.000 MB",
            "FreeUnitsPresentation": "7.836 MB",
        }
    ],
    "UnbilledAirTimeIncludingVat": 0,
}


class AppTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        return OdidoAppClient(None, USER, "fake-password", **kwargs)

    def callback(self, user=USER):
        cipher = Fernet(RETURN_KEY.encode())
        inner = cipher.encrypt(b"fake-authorization-code").decode()
        outer = cipher.encrypt(
            json.dumps(
                {
                    "ResultCode": 0,
                    "sub": user,
                    "AccessToken": inner,
                    "exp": int(time()) + 300,
                    "nbf": int(time()) - 5,
                    "iat": int(time()),
                }
            ).encode()
        ).decode()
        return "https://www.odido.nl/loginappresult?" + urlencode({"token": outer})

    async def test_app_login_page_preserves_app_return_parameter(self):
        client = self.client()
        client._get_text = AsyncMock(
            return_value=(
                client.login_url,
                '<form method="post"><input name="Username"><input type="password" name="Password"></form>',
            )
        )
        client._post_form = AsyncMock(return_value=(self.callback(), ""))
        client._capi = AsyncMock(
            side_effect=[
                OdidoInvalidAuth("code requires exchange"),
                {"AccessToken": "app-token"},
            ]
        )
        self.assertFalse(await client.async_start_login())
        self.assertIn("returnSystem=app", client._get_text.call_args.args[0])
        self.assertEqual(client._capi.await_count, 2)
        client._capi.assert_awaited_with(
            "POST",
            "createtoken",
            "CreateToken",
            1,
            payload={"AuthorizationCode": "fake-authorization-code"},
            basic=True,
        )
        self.assertEqual(client.export_session(), {"version": 1, "token": "app-token"})
        self.assertEqual(client.export_cookies(), {})

    async def test_callback_is_account_bound_and_rejects_corruption(self):
        client = self.client()
        with self.assertRaises(OdidoInvalidAuth):
            client._login_complete(self.callback("other@example.invalid"), "")
        with self.assertRaises(OdidoParseError):
            client._login_complete(
                "https://www.odido.nl/loginappresult?token=broken", ""
            )
        self.assertFalse(client.export_session())

    async def test_no_authorization_code_replay_after_failed_exchange(self):
        client = self.client()
        client._login_complete(self.callback(), "")
        client._capi = AsyncMock(side_effect=OdidoParseError("unrecognized response"))
        with self.assertRaises(OdidoParseError):
            await client._exchange_code()
        with self.assertRaises(OdidoParseError):
            await client._exchange_code()
        client._capi.assert_awaited_once()

    async def test_recreated_client_only_reads_advertised_resources(self):
        client = self.client(app_session={"version": 1, "token": "app-token"})
        client._capi = AsyncMock(
            side_effect=[
                {
                    "Resources": [
                        {
                            "Label": "Subscriber",
                            "Availability": "Available",
                            "Url": "https://capi.odido.nl/subscriber/opaque",
                        }
                    ]
                },
                {
                    "Resources": [
                        {
                            "Label": "PostpaidBundleStatus",
                            "Availability": "Available",
                            "Url": "https://capi.odido.nl/bundle/opaque",
                        }
                    ]
                },
                STATUS,
            ]
        )
        client.async_start_login = AsyncMock()
        usage = await client.async_get_usage()
        self.assertEqual(usage.remaining_gb, 7.836)
        self.assertEqual(usage.used_gb, 2.164)
        self.assertEqual(usage.limit_gb, 10)
        self.assertEqual(usage.percentage, 21.64)
        self.assertEqual(usage.extra_costs, 0)
        self.assertTrue(all(c.args[0] == "GET" for c in client._capi.call_args_list))
        self.assertEqual(client._capi.call_args.args[3], 5)
        client.async_start_login.assert_not_awaited()

    async def test_multiple_subscriptions_are_never_guessed(self):
        client = self.client(app_session={"version": 1, "token": "app-token"})
        client._capi = AsyncMock(
            return_value={
                "Resources": [
                    {
                        "Label": "Subscriber",
                        "Availability": "Available",
                        "Url": "https://capi.odido.nl/subscriber/" + key,
                    }
                    for key in ("one", "two")
                ]
            }
        )
        with self.assertRaises(OdidoParseError):
            await client.async_get_usage()
        client._capi.assert_awaited_once()

    async def test_no_token_never_sends_sms_in_background(self):
        client = self.client()
        client.async_start_login = AsyncMock()
        with self.assertRaises(OdidoInvalidAuth):
            await client.async_get_usage()
        client.async_start_login.assert_not_awaited()

    async def test_resource_host_and_transport_are_restricted(self):
        for url in (
            "http://capi.odido.nl/account/current",
            "https://evil.example/account",
            "https://capi.odido.nl:8443/x",
            "https://user@capi.odido.nl/x",
        ):
            with self.assertRaises(OdidoParseError):
                OdidoAppClient._capi_url(url)

    async def test_missing_bundle_and_unknown_units_fail_closed(self):
        for data in (
            {},
            {"Bundles": [{"Unit": "UNKNOWN"}]},
            {"Bundles": [STATUS["Bundles"][0], STATUS["Bundles"][0]]},
        ):
            with self.assertRaises(OdidoParseError):
                parse_app_usage(data)

    async def test_unknown_cost_scale_and_voice_usage_are_not_invented(self):
        result = parse_app_usage({**STATUS, "UnbilledAirTimeIncludingVat": 123})
        self.assertIsNone(result.extra_costs)
        self.assertIsNone(result.voice_used_minutes)
        self.assertIsNone(result.sms_used)
        self.assertIsNone(result.reported_data_used_gb)
        self.assertNotIn(
            "app-token",
            str(
                self.client(
                    app_session={"version": 1, "token": "app-token"}
                ).debug_summary()
            ),
        )

    async def test_current_browser_access_token_is_validated_without_old_login(self):
        client = self.client()
        client._login_complete(self.callback(), "")
        client._capi = AsyncMock(return_value={"Resources": []})
        await client._exchange_code()
        client._capi.assert_awaited_once_with("GET", "account/current", "Account", 1)
        self.assertEqual(client.export_session()["token"], "fake-authorization-code")

    async def test_all_sms_posts_keep_app_return_context(self):
        fixtures = Path(__file__).parent / "fixtures"
        client = self.client()
        client._get_text = AsyncMock(
            return_value=(client.login_url, (fixtures / "login.html").read_text())
        )
        client._post_form = AsyncMock(
            return_value=(
                client.login_url,
                (fixtures / "sms_challenge.html").read_text(),
            )
        )
        self.assertTrue(await client.async_start_login())
        client._post_form.reset_mock()
        client._post_form.side_effect = [
            (client.login_url, '{"valid":true}'),
            (self.callback(), ""),
        ]
        client._capi = AsyncMock(return_value={"Resources": []})
        await client.async_submit_code("9876")
        self.assertEqual(client._post_form.await_count, 2)
        for call in client._post_form.await_args_list:
            query = parse_qs(urlsplit(call.args[0]).query)
            self.assertEqual(query["returnSystem"], ["app"])
            self.assertEqual(query["nav"], ["off"])
        self.assertTrue(client.export_session())

    async def test_callback_variants_and_diagnostics_never_include_url_token(self):
        client = self.client()
        url = self.callback().replace("/loginappresult?", "/LoginAppResult/?")
        self.assertEqual(client._safe_url(url), url)
        self.assertTrue(client._login_complete(url, ""))
        diagnostics = str(client.debug_summary())
        self.assertIn("app_return", diagnostics)
        self.assertNotIn("fake-authorization-code", diagnostics)
        self.assertNotIn("token=", diagnostics)

    async def test_browser_launch_token_contains_the_apk_client_identifier(self):
        client = self.client()
        first = parse_qs(urlsplit(client.login_url).query)["token"][0]
        self.assertEqual(
            Fernet(RETURN_KEY.encode()).decrypt(first.encode()), CLIENT_ID.encode()
        )
        # The forms' bare action must carry the same client context through MFA.
        final = parse_qs(
            urlsplit(client._safe_url("https://www.odido.nl/login")).query
        )["token"][0]
        self.assertEqual(first, final)
        self.assertNotIn(first, str(client.debug_summary()))

    async def test_completed_website_session_is_retained_when_app_handoff_missing(self):
        client = self.client()
        client._cookies.update_cookies(
            {"session": "fake-browser-cookie"},
            response_url=URL("https://www.odido.nl/login"),
        )
        self.assertFalse(
            client._login_complete(
                "https://www.odido.nl/my/abonnement-overzicht",
                '<a href="/my/abonnement-overzicht">Account</a>',
            )
        )
        self.assertFalse(client.export_session())
        saved = client.export_bootstrap()
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["items"][0]["value"], "fake-browser-cookie")
        self.assertFalse(client._authenticated)

    async def test_resume_browser_session_never_submits_password_or_code(self):
        client = self.client()
        client._get_text = AsyncMock(return_value=(self.callback(), ""))
        client._post_form = AsyncMock()
        client._capi = AsyncMock(return_value={"Resources": []})
        await client.async_resume_bootstrap()
        client._post_form.assert_not_awaited()
        self.assertTrue(client.export_session())

    async def test_still_logged_in_handoff_failure_keeps_reusable_cookies(self):
        client = self.client()
        client._cookies.update_cookies(
            {"session": "fake-browser-cookie"},
            response_url=URL("https://www.odido.nl/login"),
        )
        client._get_text = AsyncMock(
            return_value=(
                "https://www.odido.nl/my/abonnement-overzicht",
                '<a href="/my/abonnement-overzicht">Account</a>',
            )
        )
        client._post_form = AsyncMock()
        with self.assertRaises(OdidoParseError):
            await client.async_resume_bootstrap()
        self.assertTrue(client.export_bootstrap())
        client._post_form.assert_not_awaited()

    async def test_202_token_exchange_reads_headers_without_parsing_body(self):
        from custom_components.odido_mobile_usage import app_api as module

        client = self.client()
        response = Mock(status=202, headers={"AccessToken": "verified-header-token"})
        response.json = AsyncMock(return_value=[])
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = Mock()
        session.request.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(module, "ClientSession", return_value=manager):
            data = await client._capi(
                "POST",
                "createtoken",
                "CreateToken",
                1,
                payload={"AuthorizationCode": "fake"},
                basic=True,
            )
        self.assertEqual(data, {"AccessToken": "verified-header-token"})
        response.json.assert_not_awaited()
        self.assertNotIn("verified-header-token", str(client.debug_summary()))

    async def test_header_error_cannot_promote_a_token(self):
        from custom_components.odido_mobile_usage import app_api as module

        client = self.client()
        response = Mock(
            status=202,
            headers={"AccessToken": "untrusted-token", "ErrorCode": "rejected"},
        )
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = Mock()
        session.request.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(module, "ClientSession", return_value=manager),
            self.assertRaises(OdidoInvalidAuth),
        ):
            await client._capi(
                "POST",
                "createtoken",
                "CreateToken",
                1,
                payload={"AuthorizationCode": "fake"},
                basic=True,
            )
        self.assertFalse(client.export_session())

    async def test_nested_tele2_buckets_and_private_record_aggregation(self):
        data = {
            "Bundles": [
                {
                    "Buckets": [
                        {
                            "Unit": "KB",
                            "LimitValue": 10240000,
                            "RemainingValue": 8024014,
                            "UsedValue": 2215986,
                            "LimitValuePresentation": "10000 MB",
                            "RemainingValuePresentation": "7836,0 MB",
                        }
                    ]
                }
            ]
        }
        balance = {
            "UnbilledAirTimeIncludingVat": 0,
            "Bundles": [{"IsUnlimitedCalling": "EU"}],
        }
        details = {
            "Breakdowns": [
                {
                    "CallDetailsRecords": [
                        {
                            "RatedVolume": 2468,
                            "RatedVolumeUnit": "Sec",
                            "Destination": "private-destination",
                        },
                        {"RatedVolume": 1, "RatedVolumeUnit": "SMS"},
                        {"RatedVolume": 3628467, "RatedVolumeUnit": "KB"},
                    ]
                }
            ]
        }
        usage = parse_app_usage(data, balance, details)
        self.assertAlmostEqual(usage.remaining_gb, 7.835951)
        self.assertAlmostEqual(usage.used_gb, 2.164049)
        self.assertAlmostEqual(usage.voice_used_minutes, 2468 / 60)
        self.assertEqual(usage.sms_used, 1)
        self.assertAlmostEqual(usage.reported_data_used_gb, 3.5434248046875)
        self.assertEqual(usage.voice_allowance, "Onbeperkt")
        self.assertNotIn("private-destination", str(usage.as_dict()))
        data["Bundles"][0]["Buckets"][0]["UsedValue"] = -1
        with self.assertRaises(OdidoParseError):
            parse_app_usage(data, balance, details)

    async def test_same_origin_redirect_and_resource_forbidden_preserve_session(self):
        from custom_components.odido_mobile_usage import app_api as module

        client = self.client(app_session={"version": 1, "token": "saved-token"})
        responses = []
        for status, headers in [(302, {"Location": "/canonical/account"}), (403, {})]:
            response = Mock(status=status, headers=headers)
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            responses.append(response)
        session = Mock()
        session.request.side_effect = responses
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(module, "ClientSession", return_value=manager),
            self.assertRaises(OdidoParseError),
        ):
            await client._capi("GET", "account/current", "Account", 1)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(client.export_session()["token"], "saved-token")

    async def test_cross_origin_redirect_never_receives_bearer(self):
        from custom_components.odido_mobile_usage import app_api as module

        client = self.client(app_session={"version": 1, "token": "saved-token"})
        response = Mock(
            status=302, headers={"Location": "https://example.invalid/account"}
        )
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = Mock()
        session.request.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(module, "ClientSession", return_value=manager),
            self.assertRaises(OdidoParseError),
        ):
            await client._capi("GET", "account/current", "Account", 1)
        self.assertEqual(session.request.call_count, 1)
