"""Config flow for Odido Mobile Usage."""

from __future__ import annotations

import json
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    OdidoChallengeExpired,
    OdidoClient,
    OdidoConnectionError,
    OdidoInvalidAuth,
    OdidoParseError,
)
from .const import CONF_COOKIES, CONF_USAGE_URL, DEFAULT_USAGE_URL, DOMAIN
from .cookie_store import valid_cookies

_LOGGER = logging.getLogger(__name__)


class OdidoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Odido config flow."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._client: OdidoClient | None = None
        self._credentials: dict[str, Any] = {}
        self._transport = "website"
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await self.async_step_login(user_input)
        return self.async_show_menu(step_id="user", menu_options=["app", "login", "session"])

    async def async_step_app(self, user_input=None):
        """Use the app browser login instead of the retired direct app login."""
        self._transport = "app"
        return await self.async_step_login(user_input)

    def _new_client(self, session, username, password, **kwargs):
        if self._transport == "app":
            from .app_api import OdidoAppClient
            return OdidoAppClient(session, username, password, **kwargs)
        return OdidoClient(session, username, password, **kwargs)

    def _has_completed_session(self):
        if self._transport == "app":
            return bool(self._client.export_session())
        return bool(self._client.export_cookies())

    async def async_step_session(self, user_input=None):
        """Validate an existing session before saving it; no password is needed."""
        errors = {}
        if user_input is not None:
            try:
                cookies = json.loads(user_input[CONF_COOKIES])
                if not valid_cookies(cookies):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_auth"
            else:
                username = user_input[CONF_USERNAME].strip()
                await self.async_set_unique_id(username.lower())
                self._abort_if_unique_id_configured()
                self._credentials = {CONF_USERNAME: username, CONF_PASSWORD: "", CONF_USAGE_URL: DEFAULT_USAGE_URL}
                self._client = OdidoClient(async_get_clientsession(self.hass),username,"",cookies=cookies)
                return await self._finish()
        return self.async_show_form(step_id="session",data_schema=vol.Schema({vol.Required(CONF_USERNAME):str,vol.Required(CONF_COOKIES):TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}), errors=errors)

    def _parse_failure(self, stage, err):
        # Error messages in this client contain only fixed local categories.
        known = {
            "Internet section missing": "internet_section_missing",
            "Expected one domestic data allowance": "data_allowance_ambiguous",
            "Monthly total row missing": "monthly_total_missing",
            "Monthly usage fields missing or invalid": "monthly_fields_missing",
            "Usage details missing": "usage_details_missing",
            "Expected exactly one mobile subscription": "subscription_selection_missing_or_ambiguous",
            "Odido did not select the mobile subscription": "subscription_not_selected",
            "Odido login form missing": "login_form_missing",
            "Odido verification finalization form missing": "finalization_form_missing",
        }
        reason="unexpected_response"
        for message, code in known.items():
            if str(err)==message or str(err)=="Odido usage sections incomplete: "+message:
                reason=code
                break
        if str(err).startswith("Invalid Odido verification response"):
            reason="verification_response_not_json"
        details={"stage":stage,"reason":reason,"diagnostics":self._client.debug_summary() if self._client else {}}
        _LOGGER.warning("Odido setup: %s",json.dumps(details))
        return self.async_abort(reason="unexpected_response",description_placeholders={"details":json.dumps(details)})

    async def async_step_login(
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
            self._client = self._new_client(
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
            except OdidoParseError as err:
                return self._parse_failure("login",err)
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
        return self.async_show_form(step_id="login", data_schema=schema, errors=errors)

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
            except OdidoParseError as err:
                return self._parse_failure("verification",err)
            else:
                return await self._finish()
        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Switch an existing website entry to app auth without changing IDs."""
        self._reauth_entry = self._get_reconfigure_entry()
        self._credentials = dict(self._reauth_entry.data)
        self._transport = "app"
        return await self.async_step_reauth_confirm(user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after Odido expires the session."""
        self._transport = entry_data.get("transport", "website")
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
            self._client = self._new_client(
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
            except OdidoParseError as err:
                return self._parse_failure("reauth_login",err)
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
        except (OdidoConnectionError, OdidoParseError) as err:
            # Retain completed authentication so data failures do not need an OTP.
            if self._has_completed_session():
                self._parse_failure("usage",err)
                return self._create_entry()
            if isinstance(err, OdidoParseError):
                return self._parse_failure("usage",err)
            return self.async_abort(reason="cannot_connect")
        return self._create_entry()

    def _create_entry(self) -> ConfigFlowResult:
        assert self._client is not None
        data = {**self._credentials, "transport": self._transport, "auto_reauth_blocked": False}
        if self._transport == "app":
            data["app_session"] = self._client.export_session()
            data.pop(CONF_COOKIES, None)
        else:
            data[CONF_COOKIES] = self._client.export_cookies()
            data.pop("app_session", None)
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        return self.async_create_entry(
            title=self._credentials[CONF_USERNAME], data=data
        )
