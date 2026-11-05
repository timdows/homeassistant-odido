"""Diagnostics intentionally exclude account data and usage."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "version": entry.version,
        "last_update_success": coordinator.last_update_success,
        "verification_pending": coordinator.client.debug_summary()[
            "verification_pending"
        ],
    }
