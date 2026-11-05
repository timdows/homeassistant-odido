"""Session persistence and bounded recovery regressions; synthetic cookies only."""
import json
import unittest
from http.cookies import SimpleCookie
from unittest.mock import AsyncMock, Mock
from yarl import URL
from custom_components.odido_mobile_usage.api import OdidoClient, OdidoInvalidAuth, OdidoConnectionError

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def client(self, **kwargs):
        return OdidoClient(Mock(), "test@example.invalid", "fake", **kwargs)

    async def test_roundtrip_keeps_exact_header_root_path_and_scope(self):
        client = self.client()
        client._authenticated = True
        for raw in ('session=opaque==; Path=/; Secure; HttpOnly; SameSite=Lax', 'trust="quoted token"; Path=/; Domain=odido.nl; Max-Age=3600', 'selection=one; Path=/my'):
            cookie = SimpleCookie(); cookie.load(raw)
            client._cookies.update_cookies(cookie, response_url=URL("https://www.odido.nl/login"))
        saved = json.loads(json.dumps(client.export_cookies()))
        restored = self.client(cookies=saved)
        for url in ("https://www.odido.nl/login", "https://www.odido.nl/my/tegoed", "https://odido.nl/login"):
            self.assertEqual(client._cookies.filter_cookies(URL(url)).output(), restored._cookies.filter_cookies(URL(url)).output())
        self.assertIn("expires", saved["items"][1])
        self.assertNotIn("max-age", saved["items"][1])

    async def test_legacy_tokens_not_quoted_and_sent_to_login(self):
        client = self.client(cookies={"session": "opaque=="})
        self.assertEqual(client._cookies.filter_cookies(URL("https://www.odido.nl/login"))["session"].coded_value, "opaque==")

    async def test_successful_recovery_retries_data_once_with_trust_cookies(self):
        client = self.client(cookies={"trust": "remembered"})
        client._get_usage_once = AsyncMock(side_effect=[OdidoInvalidAuth(), "usage"])
        client.async_start_login = AsyncMock(return_value=False)
        self.assertEqual(await client.async_get_usage(), "usage")
        client.async_start_login.assert_awaited_once_with(preserve_cookies=True)
        self.assertFalse(client.auto_reauth_blocked)
        self.assertEqual(client._get_usage_once.await_count, 2)

    async def test_mfa_blocks_repeated_login_including_after_restart(self):
        client = self.client(cookies={"trust": "remembered"})
        client._get_usage_once = AsyncMock(side_effect=OdidoInvalidAuth())
        client.async_start_login = AsyncMock(return_value=True)
        for _ in range(2):
            with self.assertRaises(OdidoInvalidAuth): await client.async_get_usage()
        self.assertTrue(client.auto_reauth_blocked)
        client.async_start_login.assert_awaited_once()
        restored = self.client(cookies={"trust": "remembered"}, auto_reauth_blocked=True)
        restored._get_usage_once = AsyncMock(side_effect=OdidoInvalidAuth())
        restored.async_start_login = AsyncMock()
        with self.assertRaises(OdidoInvalidAuth): await restored.async_get_usage()
        restored.async_start_login.assert_not_awaited()

    async def test_failed_retry_and_connection_error_cannot_loop(self):
        for error in (OdidoInvalidAuth(), OdidoConnectionError()):
            client = self.client(cookies={"trust": "remembered"})
            client._get_usage_once = AsyncMock(side_effect=OdidoInvalidAuth())
            client.async_start_login = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)): await client.async_get_usage()
            with self.assertRaises(OdidoInvalidAuth): await client.async_get_usage()
            client.async_start_login.assert_awaited_once()

    async def test_expired_cookie_is_not_resurrected(self):
        client = self.client(cookies={"session":"fake"})
        data = client.export_cookies()
        data["items"][0]["expires"] = "Thu, 01 Jan 1970 00:00:01 GMT"
        restored = self.client(cookies=data)
        self.assertFalse(restored._cookies.filter_cookies(URL("https://www.odido.nl/my/tegoed")))

    async def test_trusted_login_redirect_does_not_submit_credentials(self):
        client = self.client(cookies={"trust":"remembered"})
        client._get_text = AsyncMock(return_value=("https://www.odido.nl/my/abonnement-overzicht", '<a href="/my/abonnement-overzicht">Account</a>'))
        client._post_form = AsyncMock()
        self.assertFalse(await client.async_start_login(preserve_cookies=True))
        client._post_form.assert_not_awaited()
        self.assertTrue(client._authenticated)
