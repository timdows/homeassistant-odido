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

    client = OdidoClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        usage_url=DEFAULT_USAGE_URL,
        cookies=entry.data.get(CONF_COOKIES),
    )
    coordinator = OdidoDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
