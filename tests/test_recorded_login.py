"""Replay the observed form sequence with entirely synthetic field values."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from test_website import ALLOWANCE, DETAILS

from custom_components.odido_mobile_usage import api

FIXTURES = Path(__file__).parent / "fixtures"
PREFIX = "Section1.Row1.Column1.Cell1.Login."
AUTH = '<a href="/my/abonnement-overzicht">Mijn account</a>'


class RecordedFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_bom_does_not_reject_valid_code(self):
        client = await self.client()
        client._post_form.side_effect = [(api.LOGIN_URL, '\ufeff{"valid":true}\r\n'), ("https://www.odido.nl/my/abonnement-overzicht", AUTH)]
        await client.async_submit_code("9876")
        self.assertTrue(client._authenticated)

    async def test_non_json_error_includes_safe_response_classification(self):
        client = await self.client()
        client._post_form.return_value=(api.LOGIN_URL,'<html>private-secret</html>')
        client._last_response={'status':200,'format':'html','page':'login','redirected':False,'length':32}
        with self.assertRaises(api.OdidoParseError) as err:
            await client.async_submit_code('9876')
        self.assertIn('format=html; page=login',str(err.exception))
        self.assertNotIn('private-secret',str(err.exception))

    async def test_select_single_mobile_subscription_after_login(self):
        client = api.OdidoClient(Mock(), "user", "password")
        initial = '<form action="/my/abonnement-overzicht" method="post"><input type="hidden" name="Section1.ContactDashboard.Initial" value="true"></form>'
        selector = """<form action="/my/abonnement-overzicht" method="post"><input type="hidden" name="Section1.ContactDashboard.SelectedSubscriber"><button type="button" name="Section1.ContactDashboard.SwitchMobileSubscription" onclick="SelectSubscriberByLinkId(this, 'fake-link-id')">Bekijk</button></form>"""
        account_url = "https://www.odido.nl/my/abonnement-overzicht"
        client._get_text = AsyncMock(
            side_effect=[
                (account_url, AUTH + initial),
                (client.usage_url, AUTH + ALLOWANCE),
                ("https://www.odido.nl/my/verbruik-en-kosten", AUTH + DETAILS),
            ]
        )
        client._post_form = AsyncMock(
            side_effect=[(account_url, selector), (account_url, "")]
        )
        usage = await client.async_get_usage()
        self.assertEqual(usage.remaining_gb, 7.5)
        posted = client._post_form.await_args_list[1].args[1]
        self.assertEqual(
            posted["Section1.ContactDashboard.SelectedSubscriber"], "fake-link-id"
        )
        self.assertIn("Section1.ContactDashboard.SwitchMobileSubscription", posted)

    async def test_multiple_subscriptions_are_not_guessed(self):
        client = api.OdidoClient(Mock(), "user", "password")
        selector = """<form method="post"><input type="hidden" name="Section1.ContactDashboard.SelectedSubscriber"><button type="button" name="Section1.ContactDashboard.SwitchMobileSubscription" onclick="SelectSubscriberByLinkId(this, 'one')"></button><button type="button" name="Section1.ContactDashboard.SwitchMobileSubscription" onclick="SelectSubscriberByLinkId(this, 'two')"></button></form>"""
        client._post_form = AsyncMock()
        with self.assertRaises(api.OdidoParseError):
            await client._select_only_subscription(
                "https://www.odido.nl/my/abonnement-overzicht", AUTH + selector
            )
        client._post_form.assert_not_awaited()

    async def client(self):
        client = api.OdidoClient(Mock(), "test@example.invalid", "fake-password")
        client._get_text = AsyncMock(
            return_value=(api.LOGIN_URL, (FIXTURES / "login.html").read_text())
        )
        client._post_form = AsyncMock(
            return_value=(api.LOGIN_URL, (FIXTURES / "sms_challenge.html").read_text())
        )
        self.assertTrue(await client.async_start_login())
        return client

    async def test_login_button_is_submitted(self):
        client = await self.client()
        payload = client._post_form.call_args.args[1]
        self.assertEqual(payload[PREFIX + "Login"], "")
        self.assertEqual(payload[PREFIX + "Username"], "test@example.invalid")
        self.assertEqual(payload[PREFIX + "Password"], "fake-password")
        self.assertEqual(
            set(payload),
            {
                "__UniqueContentId",
                "__BindingPrefix",
                "__UniqueCorrelationId",
                "__UniqueRequestId",
                "__RequestVerificationToken",
                PREFIX + "Username",
                PREFIX + "Password",
                PREFIX + "Login",
            },
        )

    async def test_sms_validation_then_finalize_exactly_once(self):
        client = await self.client()
        client._post_form.reset_mock()
        client._post_form.side_effect = [
            (api.LOGIN_URL, '{"valid":true}'),
            ("https://www.odido.nl/my/abonnement-overzicht", AUTH),
        ]
        await client.async_submit_code("9876")
        calls = client._post_form.await_args_list
        self.assertEqual(len(calls), 2)
        validation = calls[0].args[1]
        self.assertEqual(validation["pincode"], "9876")
        self.assertEqual(validation["verification-code"], "")
        self.assertIn(PREFIX + "ValidateVerificationCode", validation)
        self.assertEqual(calls[0].kwargs, {"ajax": True, "expect_json": True})
        final = calls[1].args[1]
        self.assertEqual(final[PREFIX + "Validate"], "Validate")
        self.assertEqual(final[PREFIX + "TwoFactorAuthType"], "Sms")
        self.assertEqual(final["IsDeviceRecognitionOpted"], "Yes")
        self.assertNotIn("pincode", final)
        self.assertNotIn("9876", str(client.__dict__))
        self.assertTrue(client._authenticated)

    async def test_rejected_code_never_finalizes_or_retries(self):
        for response in ('{"valid":false}', '{"valid":"true"}', "{}"):
            client = await self.client()
            client._post_form.reset_mock()
            client._post_form.return_value = (api.LOGIN_URL, response)
            with self.assertRaises(api.OdidoInvalidAuth):
                await client.async_submit_code("9876")
            self.assertEqual(client._post_form.await_count, 1)
            self.assertIsNotNone(client._pending_form)
            self.assertEqual(client.export_cookies(), {})

    async def test_wrong_length_or_non_numeric_code_not_sent(self):
        client = await self.client()
        client._post_form.reset_mock()
        for code in ("123456", "12", "abcd", "１２３４"):
            with self.assertRaises(api.OdidoInvalidAuth):
                await client.async_submit_code(code)
        client._post_form.assert_not_awaited()

    async def test_finalization_failure_cannot_resubmit_accepted_code(self):
        client = await self.client()
        client._post_form.reset_mock()
        client._post_form.side_effect = [
            (api.LOGIN_URL, '{"valid":true}'),
            api.OdidoConnectionError("network failed"),
        ]
        with self.assertRaises(api.OdidoChallengeExpired):
            await client.async_submit_code("9876")
        self.assertEqual(client.export_cookies(), {})
        with self.assertRaises(api.OdidoParseError):
            await client.async_submit_code("9876")
        self.assertEqual(client._post_form.await_count, 2)

    async def test_non_json_verification_is_not_success(self):
        client = await self.client()
        client._post_form.reset_mock()
        client._post_form.return_value = (api.LOGIN_URL, "<html>login expired</html>")
        with self.assertRaises(api.OdidoParseError):
            await client.async_submit_code("9876")
        self.assertFalse(client._authenticated)
        self.assertEqual(client._post_form.await_count, 1)
