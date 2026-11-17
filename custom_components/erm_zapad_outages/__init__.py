"""ERM Zapad power outages for a configured place or client number."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import CONF_PLACE, CONF_POINTS, DOMAIN, LOGGER
from .coordinator import ErmZapadConfigEntry, create_coordinator, format_issue_id

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ErmZapadConfigEntry) -> bool:
    coordinator = create_coordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ErmZapadConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ErmZapadConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, format_issue_id(entry))


async def async_migrate_entry(hass: HomeAssistant, entry: ErmZapadConfigEntry) -> bool:
    if entry.version > 1:
        return False
    data = dict(entry.data)
    # 1.2: config key "area" renamed to "place" (HA has its own areas).
    if "area" in data:
        data[CONF_PLACE] = data.pop("area")
    # 1.3: metering points are no longer stored (unused personal data).
    data.pop(CONF_POINTS, None)
    if entry.minor_version < 3:
        hass.config_entries.async_update_entry(entry, data=data, minor_version=3)
        LOGGER.debug("migrated %s to version 1.3", entry.entry_id)
    return True
