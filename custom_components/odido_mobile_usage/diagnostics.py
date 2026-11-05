"""Diagnostics intentionally exclude account data and usage."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "version": entry.version,
        "last_update_success": coordinator.last_update_success if coordinator else False,
        "client": coordinator.client.debug_summary() if coordinator else {},
    }
