"""Base entity: one service device per configured place or client number."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Outage
from .const import ATTRIBUTION, CHECK_URL, DOMAIN, TIMEZONE
from .coordinator import ErmZapadCoordinator


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def tomorrow_local() -> date:
    return now_local().date() + timedelta(days=1)


# Lists in attributes are capped; a broad place name can match hundreds of rows.
MAX_LISTED = 20
# Bulky or personal attributes are kept out of the recorder.
UNRECORDED = frozenset({"upcoming", "outages", "point"})


def as_dicts(outages: list[Outage]) -> list[dict[str, str | None]]:
    return [o.as_dict() for o in outages[:MAX_LISTED]]


def pending(outages: list[Outage]) -> list[Outage]:
    """Outages that have not ended yet (no expected end counts as not ended)."""
    now = now_local()
    return [o for o in outages if o.end is None or o.end > now]


class ErmZapadEntity(CoordinatorEntity[ErmZapadCoordinator]):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _unrecorded_attributes = UNRECORDED
    # States that depend on the clock (tomorrow, not yet ended) are re-evaluated at this interval.
    _clock_interval: timedelta | None = None

    def __init__(self, coordinator: ErmZapadCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="ЕРМ Запад",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=CHECK_URL,
        )

    @property
    def available(self) -> bool:
        """The last good data is shown through failed refreshes until it gets too old."""
        return self.coordinator.data_fresh

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._clock_interval is not None:
            self.async_on_remove(
                async_track_time_interval(self.hass, self._async_clock_tick, self._clock_interval)
            )

    @callback
    def _async_clock_tick(self, _now: datetime) -> None:
        self.async_write_ha_state()
