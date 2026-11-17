"""Constants for the ERM Zapad power outages integration."""

import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "erm_zapad_outages"
LOGGER = logging.getLogger(__package__)

CONF_PLACE = "place"
CONF_DISTRICT = "district"
CONF_CLIENT_NUMBER = "client_number"
CONF_ADDRESS = "address"
CONF_POINTS = "points"

SCHEDULE_URL = "https://info.ermzapad.bg/webint/vok/avplan.php"
CHECK_URL = "https://info.ermzapad.bg/webint/vok/avplan.php?BBKING=FYI"
TIMEZONE = ZoneInfo("Europe/Sofia")

# The server answers "tomuch" after roughly 100 requests; whether the limit is per IP or per
# period is not known (requests are sent without cookies), so polling is kept sparse.
# By place, one refresh costs 1 + (published days, usually 2-3) requests.
SCAN_INTERVAL = timedelta(hours=3)
# By client number: the outage registered right now every 30 min, the 48 h plan every 3 h
# (56 requests a day per number).
CLIENT_SCAN_INTERVAL = timedelta(minutes=30)
CLIENT_PLANNED_INTERVAL = timedelta(hours=3)
# After "tomuch" no request at all is sent for this long.
RATE_LIMIT_BACKOFF = timedelta(hours=1)
# Last good data stays available through failed refreshes for this long.
STALE_AFTER = timedelta(hours=12)
# Config entries refreshing within this window share one download.
CACHE_TTL = timedelta(minutes=10)
REQUEST_TIMEOUT = 40
PARSE_TIMEOUT = 60

ATTRIBUTION = "Data provided by ЕРМ Запад"
