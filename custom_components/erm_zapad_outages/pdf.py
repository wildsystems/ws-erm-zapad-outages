"""Minimal text extraction for the ERM Zapad schedule PDFs (standard library only).

The PDFs use Type0 / Identity-H Arial subsets with ToUnicode CMaps and no object streams. Text runs
are positioned with Td/Tm inside BT blocks; lines are rebuilt by grouping runs on the same baseline.
"""

from __future__ import annotations

import logging
import re
import zlib

_LOGGER = logging.getLogger(__package__)

DATE_RE = r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}"
ROW_RE = re.compile(rf"^(?:Част от\s+)?(.+?)\s+({DATE_RE})\s+({DATE_RE})$")
TOKEN_RE = re.compile(
    rb"<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\)])*\)|\[|\]|/[^\s/\[\]<>()]+|[^\s/\[\]<>()]+"
)
NUMBER_RE = re.compile(rb"[-+]?[\d.]+")
LINE_TOLERANCE = 2.0
OBJ_RE = re.compile(rb"(\d+)\s+0\s+obj\b")
# Limits against malformed or hostile input; the real schedules are far below them.
MAX_STREAM = 8 * 1024 * 1024
MAX_CMAP_RANGE = 0xFFFF
MAX_CMAP_SIZE = 0x20000


def _inflate(raw: bytes) -> bytes:
    decomp = zlib.decompressobj()
    try:
        data = decomp.decompress(raw, MAX_STREAM)
    except zlib.error:
        return b""
    if decomp.unconsumed_tail:
        raise ValueError(f"PDF stream larger than {MAX_STREAM} bytes")
    return data


def _objects(pdf: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    objs: dict[int, tuple[bytes, bytes | None]] = {}
    pos = 0
    while m := OBJ_RE.search(pdf, pos):
        end = pdf.find(b"endobj", m.end())
        if end < 0:
            break
        body = pdf[m.end() : end]
        pos = end + len(b"endobj")
        stream = None
        s = re.search(rb"stream\r?\n", body)
        if s:
            head, raw = body[: s.start()], body[s.end() :]
            raw = raw[: raw.rfind(b"endstream")]
            if b"FlateDecode" in head:
                raw = _inflate(raw)
            stream, body = raw, head
        objs[int(m.group(1))] = (body, stream)
    return objs


def _ref(body: bytes, key: bytes) -> int | None:
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+0\s+R", body)
    return int(m.group(1)) if m else None


def _cmap(data: bytes) -> dict[int, str]:
    cmap: dict[int, str] = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            cmap[int(src, 16)] = bytes.fromhex(dst.decode()).decode("utf-16-be", "replace")
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
        ):
            first, last, base = int(lo, 16), int(hi, 16), int(dst, 16)
            if last < first or last - first > MAX_CMAP_RANGE or base + last - first > 0x10FFFF:
                continue
            for i, code in enumerate(range(first, last + 1)):
                cmap[code] = chr(base + i)
            if len(cmap) > MAX_CMAP_SIZE:
                raise ValueError("ToUnicode CMap too large")
    return cmap


def _decode(tok: bytes, cmap: dict[int, str] | None) -> str:
    if tok.startswith(b"<"):
        raw = bytes.fromhex(re.sub(rb"\s", b"", tok[1:-1]).decode())
    else:
        raw = re.sub(rb"\\([()\\])", rb"\1", tok[1:-1])
    if cmap is None:
        return raw.decode("latin-1")
    return "".join(
        cmap.get(int.from_bytes(raw[i : i + 2], "big"), "") for i in range(0, len(raw) - 1, 2)
    )


def _page_order(objs: dict[int, tuple[bytes, bytes | None]]) -> list[int]:
    kids_of: dict[int, list[int]] = {}
    for num, (body, _) in objs.items():
        if re.search(rb"/Type\s*/Pages\b", body):
            m = re.search(rb"/Kids\s*\[([^\]]*)\]", body)
            kids_of[num] = [int(n) for n in re.findall(rb"(\d+)\s+0\s+R", m.group(1))] if m else []
    children = {k for kids in kids_of.values() for k in kids}
    order: list[int] = []
    seen: set[int] = set()

    def walk(num: int) -> None:
        if num in seen:
            return
        seen.add(num)
        if num in kids_of:
            for kid in kids_of[num]:
                walk(kid)
        elif num in objs:
            order.append(num)

    for root in kids_of:
        if root not in children:
            walk(root)
    if not order:
        order = [n for n, (b, _) in objs.items() if re.search(rb"/Type\s*/Page\b", b)]
    return order


def _page_lines(content: bytes, fonts: dict[bytes, dict[int, str] | None]) -> list[str]:
    runs: list[tuple[float, float, str]] = []
    stack: list[tuple[float, float]] = []
    cm = (0.0, 0.0)
    x = y = 0.0
    font: dict[int, str] | None = None
    operands: list[bytes] = []
    for tok in TOKEN_RE.findall(content):
        if tok[:1] in b"<(/[]" or NUMBER_RE.fullmatch(tok):
            operands.append(tok)
            continue
        if tok == b"q":
            stack.append(cm)
        elif tok == b"Q" and stack:
            cm = stack.pop()
        elif tok == b"cm" and len(operands) >= 6:
            cm = (cm[0] + float(operands[-2]), cm[1] + float(operands[-1]))
        elif tok == b"BT":
            x = y = 0.0
        elif tok in (b"Td", b"TD") and len(operands) >= 2:
            x += float(operands[-2])
            y += float(operands[-1])
        elif tok == b"Tm" and len(operands) >= 6:
            x, y = float(operands[-2]), float(operands[-1])
        elif tok == b"Tf" and len(operands) >= 2:
            font = fonts.get(operands[-2][1:])
        elif tok in (b"Tj", b"'", b'"', b"TJ"):
            text = "".join(_decode(t, font) for t in operands if t[:1] in b"<(")
            if text.strip():
                runs.append((round(y + cm[1], 1), x + cm[0], text))
        operands = []
    # Group runs by baseline first, then order each line left to right: cells of one table row
    # can sit a little above or below each other.
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for ry, rx, text in sorted(runs, key=lambda r: -r[0]):
        if lines and abs(lines[-1][0] - ry) <= LINE_TOLERANCE:
            lines[-1][1].append((rx, text))
        else:
            lines.append((ry, [(rx, text)]))
    return [
        re.sub(r"\s+", " ", " ".join(t for _, t in sorted(parts, key=lambda p: p[0]))).strip()
        for _, parts in lines
    ]


def pdf_lines(pdf: bytes) -> list[str]:
    """Text lines of all pages, top to bottom, in page order."""
    objs = _objects(pdf)
    out: list[str] = []
    for num in _page_order(objs):
        body = objs[num][0]
        res_ref = _ref(body, b"Resources")
        res = objs[res_ref][0] if res_ref in objs else body
        fonts: dict[bytes, dict[int, str] | None] = {}
        fm = re.search(rb"/Font\s*<<(.*?)>>", res, re.S)
        if fm:
            for name, fnum in re.findall(rb"/([^\s/]+)\s+(\d+)\s+0\s+R", fm.group(1)):
                tu = _ref(objs.get(int(fnum), (b"", None))[0], b"ToUnicode")
                stream = objs[tu][1] if tu in objs else None
                fonts[name] = _cmap(stream) if stream else None
        contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+0\s+R)", body)
        if not contents:
            continue
        data = b"\n".join(
            objs.get(int(n), (b"", b""))[1] or b""
            for n in re.findall(rb"(\d+)\s+0\s+R", contents.group(1))
        )
        out.extend(_page_lines(data, fonts))
    return out


def schedule_rows(pdf: bytes) -> list[dict[str, str]]:
    """Rows of one daily schedule: oblast, district (община), place, start, end.

    Times are "DD.MM.YYYY HH:MM" strings.
    """
    rows: list[dict[str, str]] = []
    oblast = district = ""
    skipped = 0
    for line in pdf_lines(pdf):
        if line.startswith("област "):
            oblast = re.sub(r"\s+Част от.*$", "", line)[7:].strip()
        elif line.startswith("община "):
            district = re.sub(r"\s+За индивидуална.*$", "", line)[7:].strip()
        elif m := ROW_RE.match(line):
            rows.append(
                {
                    "oblast": oblast,
                    "district": district,
                    "place": m.group(1).strip(),
                    "start": m.group(2),
                    "end": m.group(3),
                }
            )
        elif len(re.findall(DATE_RE, line)) >= 2:
            skipped += 1
    if skipped:
        _LOGGER.warning("%d schedule lines with two times were not recognised as rows", skipped)
    return rows
