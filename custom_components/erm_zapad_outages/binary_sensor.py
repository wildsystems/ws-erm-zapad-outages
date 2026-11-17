"""Binary sensors: outage scheduled for tomorrow; outage registered right now (client number)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Outage
from .const import CONF_CLIENT_NUMBER
from .coordinator import ErmZapadConfigEntry, ErmZapadCoordinator
from .entity import ErmZapadEntity, as_dicts, tomorrow_local

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErmZapadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [OutageTomorrowBinarySensor(coordinator)]
    # By place the schedule only says that part of the place may be affected.
    if CONF_CLIENT_NUMBER in entry.data:
        entities.append(OutageNowBinarySensor(coordinator))
    async_add_entities(entities)


class OutageTomorrowBinarySensor(ErmZapadEntity, BinarySensorEntity):
    _clock_interval = timedelta(minutes=1)

    def __init__(self, coordinator: ErmZapadCoordinator) -> None:
        super().__init__(coordinator, "outage_tomorrow")

    def _tomorrow(self) -> list[Outage]:
        day = tomorrow_local()
        return [
            o for o in self.coordinator.data if o.start.date() <= day <= (o.end or o.start).date()
        ]

    @property
    def is_on(self) -> bool:
        return bool(self._tomorrow())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"outages": as_dicts(self._tomorrow())}


class OutageNowBinarySensor(ErmZapadEntity, BinarySensorEntity):
    """ERM Zapad has an outage registered for the client number (planned or unplanned)."""

    def __init__(self, coordinator: ErmZapadCoordinator) -> None:
        super().__init__(coordinator, "outage_now")

    @property
    def available(self) -> bool:
        """Only a successful last check says whether the power is off right now."""
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.current_outages())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = self.coordinator.current_outages()
        if not current:
            return {}
        return {
            "kind": current[0].kind,
            "start": current[0].start.isoformat(),
            "expected_end": current[0].end.isoformat() if current[0].end else None,
            "point": current[0].point,
        }
