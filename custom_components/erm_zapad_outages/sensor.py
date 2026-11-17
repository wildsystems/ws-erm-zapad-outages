"""Sensors of the next (or current) planned outage: readable summary, start and end."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Outage
from .coordinator import ErmZapadConfigEntry, ErmZapadCoordinator
from .entity import ErmZapadEntity, as_dicts, pending

PARALLEL_UPDATES = 0

NO_OUTAGE = "—"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErmZapadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NextOutageSensor(coordinator),
            NextOutageTimeSensor(coordinator, "next_outage_start"),
            NextOutageTimeSensor(coordinator, "next_outage_end"),
        ]
    )


def summary(outage: Outage) -> str:
    """'13.11 07:00–17:00', both dates when it spans midnight, '?' when the end is unknown."""
    if outage.end is None:
        return f"{outage.start:%d.%m %H:%M}–?"
    end_fmt = "%H:%M" if outage.end.date() == outage.start.date() else "%d.%m %H:%M"
    return f"{outage.start:%d.%m %H:%M}–{outage.end.strftime(end_fmt)}"


class _NextOutageBase(ErmZapadEntity, SensorEntity):
    _clock_interval = timedelta(minutes=1)

    def _next(self) -> Outage | None:
        outages = pending(self.coordinator.data)
        return outages[0] if outages else None


class NextOutageSensor(_NextOutageBase):
    """Readable time window of the next outage; '—' when none is published."""

    def __init__(self, coordinator: ErmZapadCoordinator) -> None:
        super().__init__(coordinator, "next_outage")

    @property
    def native_value(self) -> str:
        outage = self._next()
        return summary(outage) if outage else NO_OUTAGE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        outages = pending(self.coordinator.data)
        attrs: dict[str, Any] = {"upcoming": as_dicts(outages)}
        if outages:
            attrs |= outages[0].as_dict()
        return attrs


class NextOutageTimeSensor(_NextOutageBase):
    """Start or end of the next outage as a timestamp, for automations."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: ErmZapadCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._field = "start" if key == "next_outage_start" else "end"

    @property
    def native_value(self) -> datetime | None:
        outage = self._next()
        return getattr(outage, self._field) if outage else None
