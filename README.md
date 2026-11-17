# ERM Zapad power outages

Home Assistant custom integration that reports ЕРМ Запад (Electrohold distribution, western
Bulgaria) power outages for a client number or for a place.

Two sources, both behind <https://info.ermzapad.bg/webint/vok/avplan.php>:

- **By client number** (recommended): the per-client check of
  <https://info.ermzapad.bg/webint/vok/avplan.php?BBKING=FYI>. Exact for the address, and it also
  reports unplanned (emergency) outages that are registered right now.
- **By place**: the daily PDFs "Планирани прекъсвания за DD.MM.YYYY" of
  <https://info.ermzapad.bg/webint/vok/avplan.php?PLAN=FYI>, usually published about two days
  ahead. Indicative only (see Limitations); useful for places without a known client number.

The endpoints are undocumented and may change without notice.

## Installation

### HACS

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=wildsystems&repository=ws-erm-zapad-outages&category=integration)

Or manually: HACS → ⋮ → **Custom repositories** → repository
`https://github.com/wildsystems/ws-erm-zapad-outages`, type **Integration**. Then download
**ERM Zapad power outages** and restart Home Assistant.

### Manual

Copy `custom_components/erm_zapad_outages/` to `<config>/custom_components/` and restart Home
Assistant.

Requires Home Assistant 2026.3 or newer. No Python packages are required; the PDFs are parsed
with the standard library.

The integration brings its own icon for Home Assistant (2026.3+). The HACS dashboard may still
show a placeholder icon, as HACS does not read bundled icons yet.

## Configuration

[![Add the integration to your Home Assistant instance.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=erm_zapad_outages)

Settings → Devices & services → Add integration → **ERM Zapad power outages**, then choose
**By client number** or **By place**. Add one entry per client number or place; any number of
both kinds can coexist.

| mode | field | example | meaning |
|---|---|---|---|
| client number | Client number | `300000000000` | 12 digits from the electricity bill. The address of its metering points, with the number masked, becomes the device name, e.g. `Златуша (…0000)` |
| place | Place | `ЗЛАТУША` | place name as written in the schedules; case-insensitive substring match |
| place | Municipality / Sofia district | `БОЖУРИЩЕ` | optional; narrows the match when the name exists in several municipalities. For Sofia it is the district (`ВРЪБНИЦА`, `ОВЧА КУПЕЛ`) |

The value can be changed later with **Reconfigure** (same mode). A device name you have changed
is kept; entity IDs do not change.

## Entities (per entry)

| entity | state | attributes |
|---|---|---|
| `calendar.<name>_planned_outages` | on during an outage | events for the current and the known following outages |
| `binary_sensor.<name>_outage_now` | client number only: on while ERM Zapad has an outage (planned or unplanned) registered | `kind`, `start`, `expected_end`, `point` |
| `binary_sensor.<name>_outage_tomorrow` | on if an outage is scheduled tomorrow | `outages` |
| `sensor.<name>_next_outage` | time window of the next or current outage, e.g. `13.11 07:00–17:00`; `?` for an unknown end; `—` if none | the outage's fields, `upcoming` |
| `sensor.<name>_next_outage_start` | start of the next or current outage (timestamp) | |
| `sensor.<name>_next_outage_end` | end of the next or current outage (timestamp); unknown if not announced | |
| `event.<name>_new_outage_published` | fired when a new planned outage appears | `event_type: published`, `outages` |

Each outage is `{place, district, oblast, start, end, kind}` with ISO 8601 times (Europe/Sofia);
client-number outages add `point` (metering point) and have empty `district`/`oblast`. `kind` is
`planned` or `unplanned`; `end` is null when ERM Zapad has not announced a restore time.
The event is not fired for outages already known when Home Assistant starts, nor for unplanned
outages (use `outage_now` for those).

The `upcoming` and `outages` lists hold at most 20 outages. They and `point` are not stored in the
recorder history.

When a refresh fails, the entities keep showing the last data for up to 12 hours and then become
unavailable. `outage_now` is the exception: it is unavailable as soon as a check fails, because
an old answer says nothing about the power right now.

## Polling and request limit

ERM Zapad refuses requests ("tomuch") above roughly 100 per period; the period and whether it is
per IP are not known. All entries share one client that sends one request at a time.

- By client number: the registered outage every 30 minutes, the planned outages for the next
  48 hours every 3 hours (56 requests a day per number).
- By place: every 3 hours; one request for the list plus one per published day (usually 3–4 in
  total, 24–32 a day). Entries refreshing within 10 minutes of each other share one download.

After a "tomuch" refusal no request at all is sent for one hour.

## Alert blueprint

`blueprints/automation/erm_zapad_outages/outage_alert.yaml` notifies when a new outage is published
and again at a chosen evening time (default 19:00) before the outage day. HACS installs only the
integration; import the blueprint separately:

[![Open your Home Assistant instance and show the blueprint import dialog.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fwildsystems%2Fws-erm-zapad-outages%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ferm_zapad_outages%2Foutage_alert.yaml)

Then create an automation from it and set the notification actions, using `{{ title }}` and
`{{ message }}`:

```yaml
- action: notify.mobile_app_phone
  data:
    title: "{{ title }}"
    message: "{{ message }}"
```

A calendar trigger works as well, e.g. `trigger: calendar`, `event: start`, `offset: "-12:00:00"`.

## Limitations

- By place: the schedule names a place, not addresses, so an entry means "part of the place".
  Times can differ from the per-client data (seen: schedule 07:00–17:00, client number 09:15–17:00).
- By client number: planned outages are known only 48 hours ahead. Metering points (`32Z…`) are not
  accepted, because the site's planned check by metering point ignores the point.
- The format of the planned (48 h) answer that lists outages has not been observed yet. If an
  answer is not recognised, a repair issue "ERM Zapad answer not understood" appears under
  Settings → System → Repairs with the text received, and the plan counts as empty until an answer
  is understood again. Please report it (see below).
- Planned works can be cancelled; a cancelled outage simply disappears from the calendar.
- By place, unplanned (emergency) outages are not covered.

## Reporting problems

Open an issue at <https://github.com/wildsystems/ws-erm-zapad-outages/issues> with the log lines
or the repair issue text. Client numbers and metering points are hidden in these texts, but
answers from ERM Zapad can contain your address: remove it, and any other personal data, before
posting.

## Development

Lint and type settings are in `pyproject.toml`: `ruff check .`, `ruff format --check .`, and
`mypy erm_zapad_outages` from `custom_components/` with Home Assistant installed. GitHub Actions
run ruff, hassfest and the HACS validation.
