"""Odido Mobile Usage integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .app_api import OdidoAppClient
    from .coordinator import OdidoDataUpdateCoordinator

    # Legacy website entries require an explicit app sign-in; never use cookies
    # or submit stored passwords from a background update.
    client = OdidoAppClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data.get(CONF_PASSWORD, ""),
        app_session=entry.data.get("app_session")
        if entry.data.get("transport") == "app"
        else None,
    )
    coordinator = OdidoDataUpdateCoordinator(hass, entry, client)
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
