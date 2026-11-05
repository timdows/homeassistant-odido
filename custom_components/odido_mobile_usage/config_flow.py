"""Config flow for Odido Mobile Usage."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OdidoChallengeExpired,
    OdidoClient,
    OdidoConnectionError,
    OdidoInvalidAuth,
    OdidoParseError,
)
from .const import CONF_COOKIES, CONF_USAGE_URL, DEFAULT_USAGE_URL, DOMAIN


class OdidoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Odido config flow."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._client: OdidoClient | None = None
        self._credentials: dict[str, Any] = {}
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].strip().lower())
            self._abort_if_unique_id_configured()
            session = async_get_clientsession(self.hass)
            self._credentials = {
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_USAGE_URL: DEFAULT_USAGE_URL,
            }
            self._client = OdidoClient(
                session,
                username=self._credentials[CONF_USERNAME],
                password=self._credentials[CONF_PASSWORD],
                usage_url=self._credentials[CONF_USAGE_URL],
            )
            try:
                needs_code = await self._client.async_start_login()
            except OdidoInvalidAuth:
                errors["base"] = "invalid_auth"
            except OdidoConnectionError:
                errors["base"] = "cannot_connect"
            except OdidoParseError:
                errors["base"] = "unexpected_response"
            else:
                if needs_code:
                    return await self.async_step_verify()
                return await self._finish()
        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._client is None:
            return self.async_abort(reason="invalid_auth")
        if user_input is not None and self._client is not None:
            try:
                await self._client.async_submit_code(user_input["code"].strip())
            except OdidoChallengeExpired:
                self._client = None
                self._credentials = {}
                return self.async_abort(reason="invalid_auth")
            except OdidoInvalidAuth:
                errors["base"] = "invalid_code"
            except OdidoConnectionError:
                errors["base"] = "cannot_connect"
            except OdidoParseError:
                errors["base"] = "unexpected_response"
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after Odido expires the session."""
        self._reauth_entry = self._get_reauth_entry()
        self._credentials = dict(entry_data)
        self._credentials[CONF_USAGE_URL] = DEFAULT_USAGE_URL
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and start a fresh Odido session."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._credentials[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            session = async_get_clientsession(self.hass)
            self._client = OdidoClient(
                session,
                self._credentials[CONF_USERNAME],
                self._credentials[CONF_PASSWORD],
                usage_url=self._credentials.get(CONF_USAGE_URL, DEFAULT_USAGE_URL),
            )
            try:
                needs_code = await self._client.async_start_login()
            except OdidoInvalidAuth:
                errors["base"] = "invalid_auth"
            except OdidoConnectionError:
                errors["base"] = "cannot_connect"
            except OdidoParseError:
                errors["base"] = "unexpected_response"
            else:
                if needs_code:
                    return await self.async_step_verify()
                return await self._finish()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def _finish(self) -> ConfigFlowResult:
        try:
            await self._client.async_get_usage()
        except OdidoInvalidAuth:
            return self.async_abort(reason="invalid_auth")
        except OdidoConnectionError:
            return self.async_abort(reason="cannot_connect")
        except OdidoParseError:
            return self.async_abort(reason="unexpected_response")
        return self._create_entry()

    def _create_entry(self) -> ConfigFlowResult:
        assert self._client is not None
        data = {**self._credentials, CONF_COOKIES: self._client.export_cookies()}
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        return self.async_create_entry(
            title=self._credentials[CONF_USERNAME], data=data
        )
