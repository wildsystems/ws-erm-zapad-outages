"""Client for the ERM Zapad outage information.

Undocumented endpoints of https://info.ermzapad.bg/webint/vok/avplan.php (all POST):
  action=showpdf                      -> HTML list of daily PDFs, previewdoc(<id>) + date badge
  action=showdocid&doc_id=<id>        -> PDF "Планирани прекъсвания за DD.MM.YYYY" (by place)
  action=chkca&itn=<client number>    -> JSON {"cnt": n, "ca": ["<32Z… point> <address>", …]}
  action=viewitn&itn=<client number>  -> HTML, outage registered right now (planned or unplanned)
  action=viewitn_plan&itn=<number>    -> HTML, planned outages in the next 48 hours (excludes a
                                         running one)
The per-point variants (viewpod, viewpod_plan with pod=32Z…) exist, but viewpod_plan ignores the
point (answers "За точка на измерване  към …"), so only the 12-digit client number is used.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from .const import (
    CACHE_TTL,
    PARSE_TIMEOUT,
    RATE_LIMIT_BACKOFF,
    REQUEST_TIMEOUT,
    SCHEDULE_URL,
    TIMEZONE,
)
from .pdf import schedule_rows

LIST_RE = re.compile(r"previewdoc\((\d+)\);\">[^<]*</a>\s*<span[^>]*>(\d{2}\.\d{2}\.\d{4})<")
CLIENT_NUMBER_RE = re.compile(r"^\d{12}$")
POINT_RE = re.compile(r"32Z[0-9A-Z]{13}")
TIME_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})")
NO_OUTAGE_RE = re.compile(r"\bняма\s+(?:регистрирано|планирани)", re.I)
HEADERS = {"User-Agent": "Mozilla/5.0 (Home Assistant erm_zapad_outages)"}
# Answers are a few KB (HTML/JSON) or a few hundred KB (PDF).
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
# Malformed input surfaces as one of these from the parsers.
PARSE_ERRORS = (
    ValueError,
    KeyError,
    IndexError,
    AttributeError,
    TypeError,
    RecursionError,
    zlib.error,
)

KIND_PLANNED = "planned"
KIND_UNPLANNED = "unplanned"


class ErmZapadError(Exception):
    """Information could not be retrieved or parsed."""


class ErmZapadFormatError(ErmZapadError):
    """An answer was received but not understood (the site's format may have changed)."""


class ErmZapadRateLimited(ErmZapadError):
    """The server refused with "tomuch"; no request is sent until retry_after seconds pass."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def normalize(text: str) -> str:
    """Upper case with collapsed whitespace, the form the schedules use."""
    return re.sub(r"\s+", " ", text).strip().upper()


def strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def redact(text: str) -> str:
    """Hide client numbers and metering points in texts that end up in logs or issues."""
    return POINT_RE.sub("32Z…", re.sub(r"\b\d{12}\b", "<client number>", text))


def mask_client_number(number: str) -> str:
    return f"…{number[-4:]}"


@dataclass(frozen=True, slots=True)
class Outage:
    """One outage: a schedule row (by place) or a registration for a client number."""

    oblast: str
    district: str
    place: str
    start: datetime
    end: datetime | None  # None: no (valid) expected restore time
    kind: str = KIND_PLANNED
    point: str = ""

    def matches(self, place: str, district: str = "") -> bool:
        """Place (and district, if given) are case-insensitive substrings of the row's values."""
        return normalize(place) in normalize(self.place) and (
            not district or normalize(district) in normalize(self.district)
        )

    def as_dict(self) -> dict[str, str | None]:
        data = {
            "place": self.place,
            "district": self.district,
            "oblast": self.oblast,
            "start": self.start.isoformat(),
            "end": self.end.isoformat() if self.end else None,
            "kind": self.kind,
        }
        if self.point:
            data["point"] = self.point
        return data


@dataclass(frozen=True, slots=True)
class MeteringPoint:
    point: str
    address: str


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=TIMEZONE)


def parse_schedule(pdf: bytes) -> list[Outage]:
    """All outage rows of one daily schedule PDF.

    Rows that do not end after they start are dropped (HA calendars reject such events).
    """
    try:
        outages = [
            Outage(
                oblast=row["oblast"],
                district=row["district"],
                place=row["place"],
                start=_parse_time(row["start"]),
                end=_parse_time(row["end"]),
            )
            for row in schedule_rows(pdf)
        ]
    except PARSE_ERRORS as err:
        raise ErmZapadFormatError(f"schedule PDF not readable: {err!r}") from err
    return [o for o in outages if o.end is not None and o.end > o.start]


def parse_points(body: str) -> list[MeteringPoint]:
    """chkca answer -> metering points with their address ("32Z… ЗЛАТУША ." -> ЗЛАТУША)."""
    try:
        data = json.loads(body.lstrip("\ufeff").strip())
    except ValueError as err:
        excerpt = redact(body[:200])
        raise ErmZapadFormatError(f"unexpected client check answer: {excerpt!r}") from err
    items = data.get("ca") if isinstance(data, dict) else None
    points = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and (m := POINT_RE.match(item.strip())):
            address = item.strip()[m.end() :].strip(" .,")
            points.append(MeteringPoint(m.group(0), re.sub(r"\s+", " ", address)))
    return points


def parse_client_outages(body: str, place: str) -> list[Outage]:
    """viewitn / viewitn_plan answer -> outages.

    Seen with an outage (2026-11-13):
      <strong>За клиентски номер N, на … има регистрирано прекъсване </strong><ul>
      <li>точка на измерване - 32Z…</li><ul><li>начало на прекъсването - 13.11.2026 09:15</li>
      <li>тип на прекъсването - планирано</li>
      <li>очаквано време за възстановяване - 13.11.2026 17:00</li></ul></ul>
    The planned (48 h) answer with outages has not been seen yet; it is parsed the same way, by
    "<label> - <value>" items. An answer with neither items nor a "no outage" sentence raises
    ErmZapadFormatError with the (redacted) text.
    """
    body = body.lstrip("\ufeff").strip()
    outages: list[Outage] = []
    point = ""
    start: datetime | None = None
    end: datetime | None = None
    kind = KIND_PLANNED

    def flush() -> None:
        nonlocal start, end, kind
        if start is not None:
            valid_end = end if end is not None and end > start else None
            outages.append(Outage("", "", place, start, valid_end, kind, point))
        start, end, kind = None, None, KIND_PLANNED

    try:
        for item in re.findall(r"<li>(.*?)</li>", body, re.S):
            label, _, value = strip_tags(item).partition(" - ")
            label = label.strip().lower()
            if label.startswith("точка на измерване"):
                flush()
                point = value.strip()
            elif label.startswith("начало"):
                if start is not None:
                    flush()
                if m := TIME_RE.search(value):
                    start = _parse_time(m.group(1))
            elif "възстановяване" in label or label.startswith("край"):
                if m := TIME_RE.search(value):
                    end = _parse_time(m.group(1))
            elif label.startswith("тип"):
                kind = KIND_UNPLANNED if "непланирано" in value.lower() else KIND_PLANNED
        flush()
    except ValueError as err:
        raise ErmZapadFormatError(f"unreadable time in outage answer: {err}") from err
    if outages:
        return outages
    text = strip_tags(body)
    if NO_OUTAGE_RE.search(text):
        return []
    raise ErmZapadFormatError(f"unrecognised outage answer: {redact(text[:300])}")


class ErmZapadClient:
    """Shared by all config entries of a Home Assistant instance."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        run_blocking: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    ) -> None:
        self._session = session
        self._run_blocking = run_blocking
        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._cache: list[Outage] | None = None
        self._cache_time = 0.0
        self._blocked_until = 0.0

    async def async_get_outages(self) -> list[Outage]:
        """Schedule rows of all places from yesterday on; one download serves CACHE_TTL."""
        async with self._lock:
            age = time.monotonic() - self._cache_time
            if self._cache is not None and age < CACHE_TTL.total_seconds():
                return self._cache
            outages = await self._fetch_schedules()
            self._cache, self._cache_time = outages, time.monotonic()
            return outages

    async def async_get_points(self, client_number: str) -> list[MeteringPoint]:
        """Metering points of a client number; empty for an unknown number."""
        body = await self._post({"action": "chkca", "itn": client_number})
        return parse_points(body.decode("utf-8", "replace"))

    async def async_get_current(self, client_number: str, place: str) -> list[Outage]:
        """Outage registered right now for the client number (planned or unplanned)."""
        body = await self._post(
            {"itn": client_number, "action": "viewitn", "user": "", "lat": "0", "lon": "0"}
        )
        return parse_client_outages(body.decode("utf-8", "replace"), place)

    async def async_get_planned(self, client_number: str, place: str) -> list[Outage]:
        """Planned outages for the client number in the next 48 hours."""
        body = await self._post(
            {"itn": client_number, "action": "viewitn_plan", "user": "", "lat": "0", "lon": "0"}
        )
        return parse_client_outages(body.decode("utf-8", "replace"), place)

    async def _post(self, data: dict[str, str], limit: int = MAX_TEXT_BYTES) -> bytes:
        action = data.get("action")
        async with self._request_lock:
            if (wait := self._blocked_until - time.monotonic()) > 0:
                raise ErmZapadRateLimited(
                    f"{action} not sent: ERM Zapad refused requests (tomuch), waiting {wait:.0f} s",
                    wait,
                )
            try:
                async with self._session.post(
                    SCHEDULE_URL,
                    data=data,
                    headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    resp.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise ErmZapadError(f"{action} answer larger than {limit} bytes")
                        chunks.append(chunk)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise ErmZapadError(f"request {action} failed: {err}") from err
            body = b"".join(chunks)
            if body.strip(b"\xef\xbb\xbf \t\r\n") == b"tomuch":
                backoff = RATE_LIMIT_BACKOFF.total_seconds()
                self._blocked_until = time.monotonic() + backoff
                raise ErmZapadRateLimited("rate limited by ERM Zapad (tomuch)", backoff)
        return body

    async def _fetch_schedules(self) -> list[Outage]:
        # Yesterday's schedule is kept for outages that run past midnight.
        since = datetime.now(TIMEZONE).date() - timedelta(days=1)
        listing = (await self._post({"action": "showpdf"})).decode("utf-8", "replace")
        entries = LIST_RE.findall(listing)
        if not entries:
            raise ErmZapadFormatError("schedule list is empty or its format changed")
        docs: dict[date, int] = {}
        for doc_id, date_s in entries:
            try:
                day = datetime.strptime(date_s, "%d.%m.%Y").replace(tzinfo=TIMEZONE).date()
            except ValueError:
                continue
            if day >= since:
                docs[day] = max(int(doc_id), docs.get(day, 0))
        outages: list[Outage] = []
        for day in sorted(docs):
            pdf = await self._post(
                {"action": "showdocid", "doc_id": str(docs[day])}, limit=MAX_PDF_BYTES
            )
            if not pdf.startswith(b"%PDF"):
                raise ErmZapadFormatError(f"schedule {docs[day]} for {day} is not a PDF")
            try:
                async with asyncio.timeout(PARSE_TIMEOUT):
                    outages.extend(await self._run_blocking(parse_schedule, pdf))
            except TimeoutError as err:
                message = f"schedule {docs[day]} for {day}: parsing timed out"
                raise ErmZapadFormatError(message) from err
        return list(dict.fromkeys(outages))
