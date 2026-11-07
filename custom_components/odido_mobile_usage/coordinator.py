"""Update coordinator for Odido Mobile Usage."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OdidoError, OdidoInvalidAuth
from .app_api import OdidoAppClient
from .const import DOMAIN, UPDATE_INTERVAL
from .usage import UsageData


class OdidoDataUpdateCoordinator(DataUpdateCoordinator[UsageData]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: OdidoAppClient
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
            data["transport"] = "app"
            data["app_session"] = self.client.export_session()
            for legacy in (
                "cookies",
                "usage_url",
                "web_session",
                "auto_reauth_blocked",
            ):
                data.pop(legacy, None)
            if data != self.entry.data:
                self.hass.config_entries.async_update_entry(self.entry, data=data)
