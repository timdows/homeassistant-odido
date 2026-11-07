import unittest
from unittest.mock import AsyncMock, Mock, patch

from yarl import URL

from custom_components.odido_mobile_usage import api

AUTH = '<a href="/my/abonnement-overzicht">Mijn account</a>'
LOGIN = '<form action="/login" method="post"><input name="csrf" value="fake"><input name="Username"><input type="password" name="Password"></form>'
CODE = '<form action="/my/verify" method="post"><input name="csrf" value="fresh"><input name="VerificationCode"></form>'


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_request_matches_browser_ajax_headers(self):
        client = self.client()
        response = Mock(status=200)
        response.text = AsyncMock(return_value='{"valid":true}')
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = Mock()
        session.request.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api, "ClientSession", return_value=manager):
            await client._request(
                "POST", api.LOGIN_URL, {"pincode": "9876"}, ajax=True, expect_json=True
            )
        headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(
            headers["Accept"], "application/json, text/javascript, */*; q=0.01"
        )
        self.assertEqual(
            headers["Content-Type"], "application/x-www-form-urlencoded; charset=UTF-8"
        )
        self.assertEqual(headers["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(client._last_response["format"], "json")

    async def test_html_diagnostics_do_not_contain_page_or_secret_values(self):
        client = self.client()
        body = '<form><input type="password" name="Password" value="private-secret"></form>'
        response = Mock(status=200)
        response.text = AsyncMock(return_value=body)
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = Mock()
        session.request.return_value = response
        manager = Mock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api, "ClientSession", return_value=manager):
            await client._get_text(api.LOGIN_URL)
        self.assertEqual(client._last_response["page"], "login")
        self.assertNotIn("private-secret", str(client._last_response))

    def client(self):
        return api.OdidoClient(Mock(), "test@example.invalid", "fake-password")

    async def test_same_client_mfa_and_no_pending_cookie_export(self):
        client = self.client()
        client._get_text = AsyncMock(return_value=(api.LOGIN_URL, LOGIN))
        client._post_form = AsyncMock(
            return_value=("https://www.odido.nl/my/verify", CODE)
        )
        self.assertTrue(await client.async_start_login())
        client._cookies.update_cookies(
            {"session": "test"}, response_url=URL(api.LOGIN_URL)
        )
        self.assertEqual(client.export_cookies(), {})
        client._post_form.return_value = ("https://www.odido.nl/my/dashboard", AUTH)
        await client.async_submit_code("123456")
        self.assertEqual(client._post_form.call_args.args[1]["csrf"], "fresh")
        self.assertEqual(client.export_cookies()["items"][0]["value"], "test")

    async def test_rejected_code_keeps_updated_csrf(self):
        client = self.client()
        client._pending_form = api.parse_forms(CODE)[0]
        client._pending_url = "https://www.odido.nl/my/verify"
        client._post_form = AsyncMock(
            return_value=(client._pending_url, CODE.replace("fresh", "new"))
        )
        with self.assertRaises(api.OdidoInvalidAuth):
            await client.async_submit_code("123456")
        self.assertEqual(client._pending_form.fields["csrf"], "new")

    async def test_code_cannot_be_submitted_without_challenge(self):
        with self.assertRaises(api.OdidoParseError):
            await self.client().async_submit_code("123456")

    async def test_shared_session_is_not_modified(self):
        shared = Mock()
        client = api.OdidoClient(shared, "user", "pw", cookies={"session": "fake"})
        shared.cookie_jar.update_cookies.assert_not_called()
        self.assertIsNot(client._cookies, self.client()._cookies)

    async def test_invalid_form_action_is_rejected_before_credentials_sent(self):
        client = self.client()
        client._get_text = AsyncMock(
            return_value=(
                api.LOGIN_URL,
                LOGIN.replace("/login", "https://attacker.invalid/login"),
            )
        )
        client._post_form = AsyncMock()
        with self.assertRaises(api.OdidoParseError):
            await client.async_start_login()
        client._post_form.assert_not_awaited()

    async def test_non_authenticated_page_not_success(self):
        for html in [CODE, LOGIN, "<h1>Service unavailable</h1>", AUTH + CODE]:
            self.assertFalse(
                api._looks_logged_in("https://www.odido.nl/my/dashboard", html)
            )

    async def test_transport_errors_do_not_expose_response_or_credentials(self):
        client = self.client()
        manager = Mock()
        manager.__aenter__ = AsyncMock(side_effect=api.ClientError("private password"))
        manager.__aexit__ = AsyncMock()
        with (
            patch.object(api, "ClientSession", return_value=manager),
            self.assertRaises(api.OdidoConnectionError) as err,
        ):
            await client._get_text(api.LOGIN_URL)
        self.assertNotIn("private", str(err.exception))


class FormTests(unittest.TestCase):
    def test_only_successful_controls(self):
        form = api.parse_forms(
            '<form><input name="csrf" value="fake"><input type="checkbox" name="unchecked"><input type="checkbox" name="checked" checked><input name="disabled" disabled><button name="cancel">Cancel</button></form><input name="outside">'
        )[0]
        self.assertEqual(form.fields, {"csrf": "fake", "checked": "on"})
