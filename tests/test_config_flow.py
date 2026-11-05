"""Config-flow lifecycle tests using a minimal Home Assistant interface stub."""

import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def __init__(self):
        self.hass = Mock()
        self.context = {}

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        pass

    def async_show_form(self, **kwargs):
        return dict(type="form", **kwargs)

    def async_create_entry(self, **kwargs):
        return dict(type="create_entry", **kwargs)

    def async_abort(self, **kwargs):
        return dict(type="abort", **kwargs)

    def async_update_reload_and_abort(self, entry, **kwargs):
        return dict(type="reauth_complete", **kwargs)

    def _get_reauth_entry(self):
        return self.entry


stubs = {
    name: types.ModuleType(name)
    for name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
    )
}
stubs["homeassistant"].config_entries = stubs["homeassistant.config_entries"]
stubs["homeassistant.config_entries"].ConfigFlow = FlowBase
stubs["homeassistant.config_entries"].ConfigFlowResult = dict
stubs["homeassistant.const"].CONF_USERNAME = "username"
stubs["homeassistant.const"].CONF_PASSWORD = "password"
stubs["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: (
    Mock()
)
with patch.dict(sys.modules, stubs):
    flow_module = importlib.import_module(
        "custom_components.odido_mobile_usage.config_flow"
    )


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_finalization_aborts_instead_of_reusing_code(self):
        await self.begin()
        self.client.async_submit_code.side_effect = flow_module.OdidoChallengeExpired(
            "restart"
        )
        result = await self.flow.async_step_verify({"code": "9876"})
        self.assertEqual(result, {"type": "abort", "reason": "invalid_auth"})
        self.assertIsNone(self.flow._client)
        self.assertEqual(self.flow._credentials, {})

    def setUp(self):
        self.client = Mock()
        self.client.async_start_login = AsyncMock(return_value=True)
        self.client.async_submit_code = AsyncMock()
        self.client.async_get_usage = AsyncMock()
        self.client.export_cookies.return_value = {"session": "fake"}
        self.patch = patch.object(flow_module, "OdidoClient", return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.flow = flow_module.OdidoConfigFlow()

    async def begin(self):
        return await self.flow.async_step_user(
            {"username": " Test@Example.invalid ", "password": "fake"}
        )

    async def test_pending_login_never_persists(self):
        result = await self.begin()
        self.assertEqual(result["step_id"], "verify")
        self.client.async_get_usage.assert_not_awaited()
        self.client.export_cookies.assert_not_called()

    async def test_complete_login_validates_data_before_persisting(self):
        await self.begin()
        result = await self.flow.async_step_verify({"code": "123456"})
        self.client.async_get_usage.assert_awaited_once()
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["cookies"], {"session": "fake"})
        self.assertNotIn("code", result["data"])

    async def test_invalid_code_keeps_same_client(self):
        await self.begin()
        self.client.async_submit_code.side_effect = flow_module.OdidoInvalidAuth(
            "rejected"
        )
        result = await self.flow.async_step_verify({"code": "123456"})
        self.assertEqual(result["errors"]["base"], "invalid_code")
        self.assertIs(self.flow._client, self.client)
        self.client.export_cookies.assert_not_called()

    async def test_unrecognized_usage_does_not_create_entry(self):
        await self.begin()
        self.client.async_get_usage.side_effect = flow_module.OdidoParseError(
            "incomplete"
        )
        result = await self.flow.async_step_verify({"code": "123456"})
        self.assertEqual(result, {"type": "abort", "reason": "unexpected_response"})
        self.client.export_cookies.assert_not_called()

    async def test_reauth_replaces_existing_entry_and_usage_url(self):
        self.flow.entry = Mock()
        await self.flow.async_step_reauth(
            {
                "username": "test@example.invalid",
                "password": "old",
                "usage_url": "https://www.odido.nl/my",
                "cookies": {"session": "expired"},
            }
        )
        await self.flow.async_step_reauth_confirm({"password": "new"})
        result = await self.flow.async_step_verify({"code": "123456"})
        self.assertEqual(result["type"], "reauth_complete")
        self.assertEqual(result["data"]["password"], "new")
        self.assertEqual(result["data"]["usage_url"], "https://www.odido.nl/my/tegoed")

    async def test_verify_without_pending_client_aborts(self):
        self.assertEqual(
            await self.flow.async_step_verify({"code": "123456"}),
            {"type": "abort", "reason": "invalid_auth"},
        )
