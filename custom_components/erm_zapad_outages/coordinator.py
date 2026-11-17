"""Update coordinators: outages of one configured place or one client number."""

from __future__ import annotations

import time
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.hass_dict import HassKey

from .api import (
    ErmZapadClient,
    ErmZapadError,
    ErmZapadFormatError,
    ErmZapadRateLimited,
    Outage,
    mask_client_number,
)
from .const import (
    CLIENT_PLANNED_INTERVAL,
    CLIENT_SCAN_INTERVAL,
    CONF_ADDRESS,
    CONF_CLIENT_NUMBER,
    CONF_DISTRICT,
    CONF_PLACE,
    DOMAIN,
    LOGGER,
    SCAN_INTERVAL,
    STALE_AFTER,
)

type ErmZapadConfigEntry = ConfigEntry[ErmZapadCoordinator]

DATA_CLIENT: HassKey[ErmZapadClient] = HassKey(DOMAIN)


def get_client(hass: HomeAssistant) -> ErmZapadClient:
    """One client per HA instance: shared downloads and one request at a time."""
    if (client := hass.data.get(DATA_CLIENT)) is None:
        session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
        client = hass.data[DATA_CLIENT] = ErmZapadClient(session, hass.async_add_executor_job)
    return client


def create_coordinator(hass: HomeAssistant, entry: ErmZapadConfigEntry) -> ErmZapadCoordinator:
    if CONF_CLIENT_NUMBER in entry.data:
        return ClientNumberCoordinator(hass, entry)
    return PlaceCoordinator(hass, entry)


def format_issue_id(entry: ConfigEntry) -> str:
    return f"unrecognised_answer_{entry.entry_id}"


def update_failed(err: ErmZapadError) -> UpdateFailed:
    """After "tomuch" the next refresh waits until the client accepts requests again."""
    retry_after = err.retry_after if isinstance(err, ErmZapadRateLimited) else None
    return UpdateFailed(str(err), retry_after=retry_after)


class ErmZapadCoordinator(DataUpdateCoordinator[list[Outage]]):
    """Outages sorted by start, current ones included."""

    config_entry: ErmZapadConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ErmZapadConfigEntry, interval: timedelta, label: str
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {label}",
            update_interval=interval,
        )
        self.client = get_client(hass)
        # Planned outages absent from the previous successful update; empty after start-up.
        self.new_outages: list[Outage] = []
        self._last_success: float | None = None

    @property
    def data_fresh(self) -> bool:
        """Data exists and the last successful update is recent enough to show it."""
        return (
            self.data is not None
            and self._last_success is not None
            and time.monotonic() - self._last_success < STALE_AFTER.total_seconds()
        )

    def current_outages(self) -> list[Outage]:
        """Outages registered as in progress right now (client number only)."""
        return []

    async def _async_update_data(self) -> list[Outage]:
        self.new_outages = []
        outages = await self._async_fetch()
        self._last_success = time.monotonic()
        return outages

    async def _async_fetch(self) -> list[Outage]:
        raise NotImplementedError

    def _report_format_error(self, err: ErmZapadFormatError) -> None:
        LOGGER.warning("%s: %s", self.name, err)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            format_issue_id(self.config_entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unrecognised_answer",
            translation_placeholders={"title": self.config_entry.title, "error": str(err)},
        )

    def _clear_format_error(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, format_issue_id(self.config_entry))


class PlaceCoordinator(ErmZapadCoordinator):
    """Daily schedule rows matching the entry's place/district."""

    def __init__(self, hass: HomeAssistant, entry: ErmZapadConfigEntry) -> None:
        super().__init__(hass, entry, SCAN_INTERVAL, entry.title)
        self.place: str = entry.data[CONF_PLACE]
        self.district: str = entry.data.get(CONF_DISTRICT, "")

    async def _async_fetch(self) -> list[Outage]:
        try:
            outages = await self.client.async_get_outages()
        except ErmZapadFormatError as err:
            self._report_format_error(err)
            raise update_failed(err) from err
        except ErmZapadError as err:
            raise update_failed(err) from err
        self._clear_format_error()
        found = sorted(
            dict.fromkeys(o for o in outages if o.matches(self.place, self.district)),
            key=lambda o: o.start,
        )
        if self.data is not None:
            self.new_outages = [o for o in found if o not in self.data]
        return found


class ClientNumberCoordinator(ErmZapadCoordinator):
    """Outage registered now (every update) plus the 48 h plan (every CLIENT_PLANNED_INTERVAL)."""

    def __init__(self, hass: HomeAssistant, entry: ErmZapadConfigEntry) -> None:
        number: str = entry.data[CONF_CLIENT_NUMBER]
        super().__init__(hass, entry, CLIENT_SCAN_INTERVAL, mask_client_number(number))
        self.client_number = number
        self.address: str = entry.data.get(CONF_ADDRESS) or mask_client_number(number)
        self.current: list[Outage] = []
        self.planned: list[Outage] | None = None
        self._planned_at = 0.0
        self._planned_unrecognised = False

    def current_outages(self) -> list[Outage]:
        return self.current

    async def _async_fetch(self) -> list[Outage]:
        try:
            current = await self.client.async_get_current(self.client_number, self.address)
        except ErmZapadFormatError as err:
            self._report_format_error(err)
            raise update_failed(err) from err
        except ErmZapadError as err:
            raise update_failed(err) from err
        if (
            self.planned is None
            or time.monotonic() - self._planned_at >= CLIENT_PLANNED_INTERVAL.total_seconds()
        ):
            try:
                planned = await self.client.async_get_planned(self.client_number, self.address)
            except ErmZapadFormatError as err:
                # Not understood: retrying soon would not help; show the current outage anyway.
                self._planned_unrecognised = True
                self._report_format_error(err)
                if self.planned is None:
                    self.planned = []
                self._planned_at = time.monotonic()
            except ErmZapadError as err:
                LOGGER.warning("%s: planned outages not updated: %s", self.name, err)
            else:
                if self.planned is not None:
                    self.new_outages = [o for o in planned if o not in self.planned]
                self.planned, self._planned_at = planned, time.monotonic()
                self._planned_unrecognised = False
        if not self._planned_unrecognised:
            self._clear_format_error()
        self.current = current
        seen = {(o.start, o.end) for o in current}
        merged = current + [o for o in self.planned or [] if (o.start, o.end) not in seen]
        return sorted(dict.fromkeys(merged), key=lambda o: o.start)
