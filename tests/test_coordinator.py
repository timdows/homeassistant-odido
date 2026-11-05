"""Coordinator persists recovery protection and rotated cookies on data errors."""
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from custom_components.odido_mobile_usage.api import OdidoInvalidAuth, OdidoParseError


class Base:
    def __class_getitem__(cls, item): return cls
    def __init__(self, hass, **kwargs): self.hass = hass
class AuthFailed(Exception): pass
class UpdateFailed(Exception): pass
stubs = {name:types.ModuleType(name) for name in ("homeassistant", "homeassistant.config_entries", "homeassistant.core", "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.update_coordinator")}
stubs["homeassistant.config_entries"].ConfigEntry = object
stubs["homeassistant.core"].HomeAssistant = object
stubs["homeassistant.exceptions"].ConfigEntryAuthFailed = AuthFailed
stubs["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = Base
stubs["homeassistant.helpers.update_coordinator"].UpdateFailed = UpdateFailed
with patch.dict(sys.modules, stubs):
    module = importlib.import_module("custom_components.odido_mobile_usage.coordinator")

class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_block_saved_on_auth_failure_without_overwriting_session(self):
        entry = types.SimpleNamespace(data={"cookies":{"session":"old"}})
        client = Mock(auto_reauth_blocked=True)
        client.async_get_usage = AsyncMock(side_effect=OdidoInvalidAuth())
        client.export_cookies.return_value = {}
        hass = Mock()
        with self.assertRaises(AuthFailed):
            await module.OdidoDataUpdateCoordinator(hass, entry, client)._async_update_data()
        data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertTrue(data["auto_reauth_blocked"])
        self.assertEqual(data["cookies"], entry.data["cookies"])

    async def test_rotated_cookie_saved_even_when_usage_parser_fails(self):
        entry = types.SimpleNamespace(data={"cookies":{"session":"old"}})
        client = Mock(auto_reauth_blocked=False)
        client.async_get_usage = AsyncMock(side_effect=OdidoParseError())
        client.export_cookies.return_value = {"session":"rotated"}
        hass = Mock()
        with self.assertRaises(UpdateFailed):
            await module.OdidoDataUpdateCoordinator(hass, entry, client)._async_update_data()
        self.assertEqual(hass.config_entries.async_update_entry.call_args.kwargs["data"]["cookies"], {"session":"rotated"})
    async def test_app_session_survives_usage_parse_failure_without_cookies(self):
        entry = types.SimpleNamespace(data={'transport':'app','cookies':{'old':'website'},'app_session':{'version':1,'token':'old'}})
        client = Mock(auto_reauth_blocked=False)
        client.async_get_usage = AsyncMock(side_effect=OdidoParseError())
        client.export_session.return_value = {'version':1,'token':'new'}
        hass = Mock()
        with self.assertRaises(UpdateFailed):
            await module.OdidoDataUpdateCoordinator(hass, entry, client)._async_update_data()
        data = hass.config_entries.async_update_entry.call_args.kwargs['data']
        self.assertEqual(data['app_session'], {'version':1,'token':'new'})
        self.assertNotIn('cookies', data)
        client.export_cookies.assert_not_called()
