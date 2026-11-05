"""Odido Mobile Usage integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_COOKIES, DEFAULT_USAGE_URL, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .api import OdidoClient
    from .coordinator import OdidoDataUpdateCoordinator

    client_type = OdidoClient
    auth = {"cookies": entry.data.get(CONF_COOKIES)}
    if entry.data.get("transport") == "app":
        from .app_api import OdidoAppClient
        client_type = OdidoAppClient
        auth = {"app_session": entry.data.get("app_session")}
    client = client_type(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        usage_url=DEFAULT_USAGE_URL,
        **auth,
        auto_reauth_blocked=entry.data.get("auto_reauth_blocked", False),
    )
    coordinator = OdidoDataUpdateCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
