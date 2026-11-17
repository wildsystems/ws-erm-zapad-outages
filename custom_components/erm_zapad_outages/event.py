"""Event entity: fired when a schedule with new outages for the place is published."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ErmZapadConfigEntry, ErmZapadCoordinator
from .entity import ErmZapadEntity, as_dicts

PARALLEL_UPDATES = 0

EVENT_PUBLISHED = "published"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErmZapadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NewOutageEvent(entry.runtime_data)])


class NewOutageEvent(ErmZapadEntity, EventEntity):
    """Not fired for outages already known at start-up; the calendar/binary sensor cover those."""

    _attr_event_types = [EVENT_PUBLISHED]

    def __init__(self, coordinator: ErmZapadCoordinator) -> None:
        super().__init__(coordinator, "new_outage")

    @callback
    def _handle_coordinator_update(self) -> None:
        if new := self.coordinator.new_outages:
            self._trigger_event(EVENT_PUBLISHED, {"outages": as_dicts(new)})
        super()._handle_coordinator_update()
