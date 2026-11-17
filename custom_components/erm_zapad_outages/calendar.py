"""Calendar of the outages (current one and the published or planned following ones)."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import KIND_UNPLANNED, Outage
from .const import CHECK_URL, LOGGER
from .coordinator import ErmZapadConfigEntry, ErmZapadCoordinator
from .entity import ErmZapadEntity, now_local, pending

PARALLEL_UPDATES = 0

# Shown for an outage without an expected restore time, extended as time passes.
UNKNOWN_END_SPAN = timedelta(hours=1)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ErmZapadConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([OutageCalendar(entry.runtime_data)])


def _end(outage: Outage) -> datetime:
    return outage.end or max(outage.start, now_local()) + UNKNOWN_END_SPAN


def _event(outage: Outage) -> CalendarEvent:
    kind = "Непланирано" if outage.kind == KIND_UNPLANNED else "Планирано"
    if outage.point:
        description = f"Точка на измерване {outage.point}."
        if outage.end is None:
            description += " Очакваното време за възстановяване не е обявено."
        location = outage.place
    else:
        description = (
            "Индикативно: засяга част от посоченото място, не непременно целия район. "
            f"Проверка по клиентски номер: {CHECK_URL}"
        )
        location = f"{outage.place}, общ. {outage.district}, обл. {outage.oblast}"
    return CalendarEvent(
        start=outage.start,
        end=_end(outage),
        summary=f"{kind} прекъсване на тока – {outage.place}",
        description=description,
        location=location,
        uid="|".join(
            (
                outage.start.isoformat(),
                outage.end.isoformat() if outage.end else "",
                outage.point or outage.district,
                outage.place,
            )
        ),
    )


def _events(outages: list[Outage]) -> list[CalendarEvent]:
    """Events of the outages; one that HA rejects is skipped instead of failing the calendar."""
    events = []
    for outage in outages:
        try:
            events.append(_event(outage))
        except HomeAssistantError as err:
            LOGGER.debug("outage at %s skipped in the calendar: %s", outage.start, err)
    return events


class OutageCalendar(ErmZapadEntity, CalendarEntity):
    def __init__(self, coordinator: ErmZapadCoordinator) -> None:
        super().__init__(coordinator, "planned_outages")

    @property
    def event(self) -> CalendarEvent | None:
        events = _events(pending(self.coordinator.data))
        return events[0] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return _events(
            [o for o in self.coordinator.data if o.start < end_date and _end(o) > start_date]
        )
