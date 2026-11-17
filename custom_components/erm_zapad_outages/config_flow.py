"""Config flow: one entry per client number or per monitored place."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .api import (
    CLIENT_NUMBER_RE,
    ErmZapadError,
    ErmZapadRateLimited,
    mask_client_number,
    normalize,
)
from .const import (
    CONF_ADDRESS,
    CONF_CLIENT_NUMBER,
    CONF_DISTRICT,
    CONF_PLACE,
    DOMAIN,
)
from .coordinator import get_client

PLACE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLACE): str,
        vol.Optional(CONF_DISTRICT, default=""): str,
    }
)
CLIENT_SCHEMA = vol.Schema({vol.Required(CONF_CLIENT_NUMBER): str})


def _place_title(place: str, district: str) -> str:
    return f"{place.title()} ({district.title()})" if district else place.title()


def _client_title(address: str, client_number: str) -> str:
    """The number is masked: the title becomes the device name and so part of entity IDs."""
    masked = mask_client_number(client_number)
    return f"{address.title()} ({masked})" if address else masked


def _auto_titles(data: Mapping[str, Any]) -> set[str]:
    """Titles the flow would have given this entry data, including the pre-0.1.0 client format."""
    if CONF_CLIENT_NUMBER not in data:
        return {_place_title(data.get(CONF_PLACE, ""), data.get(CONF_DISTRICT, ""))}
    address, number = data.get(CONF_ADDRESS, ""), data[CONF_CLIENT_NUMBER]
    return {_client_title(address, number), f"{address.title()} ({number})", number}


class ErmZapadConfigFlow(ConfigFlow, domain=DOMAIN):
    """By client number (exact, includes unplanned outages) or by place name (daily schedules)."""

    VERSION = 1
    MINOR_VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["client", "place"])

    # --- by client number ------------------------------------------------------------------

    async def _async_validate_client(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        number = "".join(user_input[CONF_CLIENT_NUMBER].split())
        if not CLIENT_NUMBER_RE.match(number):
            errors[CONF_CLIENT_NUMBER] = "invalid_client_number"
            return None
        try:
            points = await get_client(self.hass).async_get_points(number)
        except ErmZapadRateLimited:
            errors["base"] = "rate_limited"
            return None
        except ErmZapadError:
            errors["base"] = "cannot_connect"
            return None
        if not points:
            errors[CONF_CLIENT_NUMBER] = "unknown_client_number"
            return None
        addresses = list(dict.fromkeys(p.address for p in points if p.address))
        return {CONF_CLIENT_NUMBER: number, CONF_ADDRESS: ", ".join(addresses)}

    async def async_step_client(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if (
            user_input is not None
            and (data := await self._async_validate_client(user_input, errors)) is not None
        ):
            await self.async_set_unique_id(f"client|{data[CONF_CLIENT_NUMBER]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_client_title(data[CONF_ADDRESS], data[CONF_CLIENT_NUMBER]), data=data
            )
        return self.async_show_form(
            step_id="client",
            data_schema=self.add_suggested_values_to_schema(CLIENT_SCHEMA, user_input),
            errors=errors,
        )

    # --- by place ----------------------------------------------------------------------------

    async def _async_validate_place(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, str] | None:
        place = normalize(user_input[CONF_PLACE])
        district = normalize(user_input.get(CONF_DISTRICT, ""))
        if not place:
            errors[CONF_PLACE] = "invalid_place"
            return None
        try:
            await get_client(self.hass).async_get_outages()
        except ErmZapadRateLimited:
            errors["base"] = "rate_limited"
        except ErmZapadError:
            errors["base"] = "cannot_connect"
        if errors:
            return None
        return {CONF_PLACE: place, CONF_DISTRICT: district}

    async def async_step_place(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if (
            user_input is not None
            and (data := await self._async_validate_place(user_input, errors)) is not None
        ):
            await self.async_set_unique_id(f"{data[CONF_DISTRICT]}|{data[CONF_PLACE]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_place_title(data[CONF_PLACE], data[CONF_DISTRICT]), data=data
            )
        return self.async_show_form(
            step_id="place",
            data_schema=self.add_suggested_values_to_schema(PLACE_SCHEMA, user_input),
            errors=errors,
        )

    # --- reconfigure (same kind as the entry) ------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        is_client = CONF_CLIENT_NUMBER in entry.data
        errors: dict[str, str] = {}
        if user_input is not None:
            if is_client:
                data = await self._async_validate_client(user_input, errors)
            else:
                data = await self._async_validate_place(user_input, errors)
            if data is not None:
                if is_client:
                    unique_id = f"client|{data[CONF_CLIENT_NUMBER]}"
                    title = _client_title(data[CONF_ADDRESS], data[CONF_CLIENT_NUMBER])
                else:
                    unique_id = f"{data[CONF_DISTRICT]}|{data[CONF_PLACE]}"
                    title = _place_title(data[CONF_PLACE], data[CONF_DISTRICT])
                if any(
                    e.unique_id == unique_id and e.entry_id != entry.entry_id
                    for e in self._async_current_entries(include_ignore=False)
                ):
                    return self.async_abort(reason="already_configured")
                # A title the user has changed is kept.
                if entry.title not in _auto_titles(entry.data):
                    title = entry.title
                return self.async_update_reload_and_abort(
                    entry, unique_id=unique_id, title=title, data=data
                )
        schema = CLIENT_SCHEMA if is_client else PLACE_SCHEMA
        suggested = user_input or {k: v for k, v in entry.data.items() if k in schema.schema}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )
