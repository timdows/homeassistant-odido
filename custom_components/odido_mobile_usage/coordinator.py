"""Update coordinator for Odido Mobile Usage."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OdidoClient, OdidoError, OdidoInvalidAuth
from .const import CONF_COOKIES, DOMAIN, UPDATE_INTERVAL
from .website import UsageData


class OdidoDataUpdateCoordinator(DataUpdateCoordinator[UsageData]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: OdidoClient
    ) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self) -> UsageData:
        try:
            result = await self.client.async_get_usage()
            return result
        except OdidoInvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OdidoError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            data = dict(self.entry.data)
            data["auto_reauth_blocked"] = self.client.auto_reauth_blocked
            if data.get("transport") == "app":
                data["app_session"] = self.client.export_session()
                data.pop(CONF_COOKIES, None)
            else:
                cookies = self.client.export_cookies()
                if cookies:
                    data[CONF_COOKIES] = cookies
            if data != self.entry.data:
                self.hass.config_entries.async_update_entry(self.entry, data=data)
