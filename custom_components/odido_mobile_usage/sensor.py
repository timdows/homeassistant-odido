"""Sensors for Odido Mobile Usage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OdidoDataUpdateCoordinator
from .usage import UsageData


@dataclass(frozen=True, kw_only=True)
class OdidoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[UsageData], Any]


SENSORS = (
    OdidoSensorDescription(
        key="used",
        translation_key="used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda data: data.used_gb,
    ),
    OdidoSensorDescription(
        key="remaining",
        translation_key="remaining",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda data: data.remaining_gb,
    ),
    OdidoSensorDescription(
        key="limit",
        translation_key="limit",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda data: data.limit_gb,
    ),
    OdidoSensorDescription(
        key="percentage",
        translation_key="percentage",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda data: data.percentage,
    ),
    OdidoSensorDescription(
        key="reported_data_used",
        translation_key="reported_data_used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        value_fn=lambda data: data.reported_data_used_gb,
    ),
    OdidoSensorDescription(
        key="voice_used",
        translation_key="voice_used",
        native_unit_of_measurement="min",
        value_fn=lambda data: data.voice_used_minutes,
    ),
    OdidoSensorDescription(
        key="voice_allowance",
        translation_key="voice_allowance",
        value_fn=lambda data: data.voice_allowance,
    ),
    OdidoSensorDescription(
        key="sms_used", translation_key="sms_used", value_fn=lambda data: data.sms_used
    ),
    OdidoSensorDescription(
        key="extra_costs",
        translation_key="extra_costs",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda data: data.extra_costs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: OdidoDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        OdidoUsageSensor(coordinator, entry, description) for description in SENSORS
    )


class OdidoUsageSensor(CoordinatorEntity[OdidoDataUpdateCoordinator], SensorEntity):
    entity_description: OdidoSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OdidoDataUpdateCoordinator,
        entry: ConfigEntry,
        description: OdidoSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Odido mobile subscription",
            "manufacturer": "Odido",
            "model": "Mobiel (voormalig Tele2)",
        }

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        if self.entity_description.key in ("used", "remaining", "limit", "percentage"):
            return {
                "source": "Mijn Odido tegoed",
                "data_units": "decimal: 1000 MB = 1 GB",
            }
        return (
            {"source": "Mijn Odido Verbruik & Kosten"}
            if self.entity_description.key != "voice_allowance"
            else {}
        )
