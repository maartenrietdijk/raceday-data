#!/usr/bin/env python3
"""Build reviewable RaceDay session-time proposals from official sources only.

The scanner never edits a calendar JSON file. It reads only series that contain
TBC sessions and writes its idempotent result to
`.raceday/session-time-proposals.json` for review in raceday-editor.html.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import io
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import unicodedata
import zlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Keep adapter exceptions identical when this file runs as a CLI script.
if __name__ == "__main__":
    sys.modules.setdefault("official_schedule_scanner", sys.modules[__name__])

from bsb_fd_schedule import parse_bsb_schedule, parse_bsb_pdf, parse_fd_schedule, specific_kind, match_fd

LOG = logging.getLogger("raceday.session_times")
USER_AGENT = "RaceDay official schedule scanner/1.0 (+https://raceday.app)"
PROPOSAL_SCHEMA_VERSION = 1
_BROWSER_SESSION = None


def tls_context() -> ssl.SSLContext:
    """Use a verified platform CA bundle, including Xcode Python on macOS."""
    configured = os.environ.get("SSL_CERT_FILE")
    candidates = [configured, "/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]
    ca_file = next((path for path in candidates if path and Path(path).is_file()), None)
    return ssl.create_default_context(cafile=ca_file)

SUPPORTED_KINDS = {
    "practice", "qualifying", "hyperpole", "sprintQualifying", "sprintRace",
    "featureRace", "shakedown", "stage", "testing", "race",
}
IGNORED_SESSION_WORDS = {
    "autograph", "briefing", "conference", "drivers parade", "grid walk",
    "hot lap", "pit walk", "podium", "press", "support", "television", "tv",
    "bronze drivers only",
}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}


@dataclass(frozen=True)
class SourceSession:
    name: str
    date: str
    local_time: str
    timezone: str
    duration_minutes: Optional[int] = None
    utc_hint: Optional[str] = None


@dataclass(frozen=True)
class FetchResult:
    stable_url: str
    final_url: str
    title: str
    body: str
    last_modified: Optional[str]


class SourceError(RuntimeError):
    pass


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", text))


RALLY_LOWERCASE_WORDS = {
    "a", "an", "and", "at", "da", "das", "de", "del", "della", "des", "di",
    "do", "dos", "du", "e", "el", "en", "et", "la", "las", "le", "les",
    "of", "the", "van", "von", "y",
}


def rally_name_case(value: str) -> str:
    """Turn an all-caps rally name into readable multilingual title case."""
    value = " ".join(str(value or "").split())
    letters = [character for character in value if character.isalpha()]
    if not letters or not all(character.isupper() for character in letters):
        return value

    parts = re.split(r"(\s+|[-–—/])", value)
    at_name_start = True
    converted: List[str] = []
    for part in parts:
        if not part:
            continue
        if re.fullmatch(r"\s+", part):
            converted.append(part)
            continue
        if part in {"-", "–", "—", "/"}:
            converted.append(part)
            at_name_start = True
            continue

        word_key = normalize(part)
        if word_key in RALLY_LOWERCASE_WORDS and not at_name_start:
            converted.append(part.lower())
        elif word_key in {"ss", "sss"}:
            converted.append(part.upper())
        else:
            lowered = part.lower()
            chars = list(lowered)
            capitalize_next = True
            for index, character in enumerate(chars):
                if character.isalpha() and capitalize_next:
                    chars[index] = character.upper()
                    capitalize_next = False
                elif character in {"'", "’"} and index + 1 < len(chars):
                    capitalize_next = True
            converted.append("".join(chars))
        if any(character.isalpha() for character in part):
            at_name_start = False
    return "".join(converted)


def rally_session_name_case(value: str) -> str:
    match = re.match(r"^(SS\d+\s*-\s*)(.+)$", str(value or ""), re.I)
    if not match:
        return rally_name_case(value)
    return "{}{}".format(match.group(1).upper(), rally_name_case(match.group(2)))


def url_slug(value: object) -> str:
    return normalize(value).replace(" ", "-")


def formula_e_slug(value: object) -> str:
    raw = re.sub(r"\be\s*prix\b", " ", normalize(value))
    raw = re.sub(r"\b\d+\b", " ", raw)
    return "-".join(raw.split())


def formula_e_season(calendar_year: int) -> str:
    return "{}-{:02d}".format(calendar_year - 1, calendar_year % 100)


def tokens(value: object) -> set:
    return {part for part in normalize(value).split() if len(part) > 1}


def similarity(left: object, right: object) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_clock(value: str) -> str:
    raw = html.unescape(value).strip().lower().replace(".", "")
    match = re.search(r"\b(\d{1,2})[:.](\d{2})\s*([ap]m)?\b", raw)
    if not match:
        hour_only = re.search(r"\b(\d{1,2})\s*([ap]m)\b", raw)
        if hour_only:
            match = re.match(r"(\d{1,2})(00)([ap]m)", "{}00{}".format(*hour_only.groups()))
    if not match:
        raise ValueError("no clock time in {!r}".format(value))
    hour, minute = int(match.group(1)), int(match.group(2))
    suffix = match.group(3)
    if suffix:
        if hour == 12:
            hour = 0
        if suffix == "pm":
            hour += 12
    if hour > 23 or minute > 59:
        raise ValueError("invalid clock time {!r}".format(value))
    return "{:02d}:{:02d}".format(hour, minute)


def parse_heading_date(value: str, year: int) -> Optional[str]:
    raw = normalize(value)
    match = re.search(
        r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday )?"
        r"(\d{1,2}) (january|february|march|april|may|june|july|august|september|october|november|december)"
        r"(?: (\d{4}))?",
        raw,
    )
    if not match:
        return None
    return date(int(match.group(3) or year), MONTHS[match.group(2)], int(match.group(1))).isoformat()


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def page_title(body: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    return strip_tags(match.group(1)) if match else "Official timetable"


def extract_links(body: str, base_url: str) -> List[Tuple[str, str]]:
    links = []
    for href, label in re.findall(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", body, re.I | re.S
    ):
        links.append((urljoin(base_url, html.unescape(href)), strip_tags(label)))
    return links


def allowed_url(url: str, domains: Sequence[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain.lower() for domain in domains)


def fetch_url(url: str, allowed_domains: Sequence[str], extra_headers: Optional[Dict[str, str]] = None) -> FetchResult:
    if not allowed_url(url, allowed_domains):
        raise SourceError("source domain is not allowlisted: {}".format(url))
    # Official event slugs can contain spaces or Unicode (e.g. Indianapolis 8 Hour).
    # Preserve URL delimiters and existing escapes while encoding unsafe characters.
    url = quote(url, safe=":/?&=#%")
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/json"}
    headers.update(extra_headers or {})
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=25, context=tls_context()) as response:
            final_url = response.geturl()
            if not allowed_url(final_url, allowed_domains):
                raise SourceError("redirect left official allowlist: {}".format(final_url))
            data = response.read(8_000_001)
            if len(data) > 8_000_000:
                raise SourceError("official source is larger than the 8 MB safety limit")
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
            try:
                if "gzip" in content_encoding:
                    data = gzip.decompress(data)
                elif "deflate" in content_encoding:
                    data = zlib.decompress(data)
            except (OSError, zlib.error) as exc:
                raise SourceError("official source compression could not be decoded") from exc
            if len(data) > 8_000_000:
                raise SourceError("expanded official source is larger than the 8 MB safety limit")
            body = data.decode(response.headers.get_content_charset() or "utf-8", "replace")
            return FetchResult(
                stable_url=url,
                final_url=final_url,
                title=page_title(body),
                body=body,
                last_modified=response.headers.get("Last-Modified"),
            )
    except HTTPError as exc:
        host = (urlparse(url).hostname or "").lower()
        if exc.code == 403 and host in {'24hseries.com', 'www.24hseries.com'}:
            return fetch_url_with_curl(url, allowed_domains)
        browser_protected = {"imsa.com", "www.imsa.com", "btcc.net", "www.btcc.net"}
        if exc.code in {403, 429} and host in browser_protected:
            return fetch_url_with_browser_fingerprint(url, allowed_domains)
        raise SourceError("could not read official source {}: {}".format(url, exc)) from exc
    except URLError as exc:
        # Some older official SRO servers omit an intermediate certificate that
        # Xcode's Python cannot build, while the platform curl trust stack can.
        # Curl still performs full certificate verification; never use -k.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return fetch_url_with_curl(url, allowed_domains)
        raise SourceError("could not read official source {}: {}".format(url, exc)) from exc
    except (OSError, HTTPException) as exc:
        raise SourceError("could not read official source {}: {}".format(url, exc)) from exc


def fetch_url_with_browser_fingerprint(url: str, allowed_domains: Sequence[str]) -> FetchResult:
    """Retry a protected official site with a real browser TLS/HTTP fingerprint."""
    global _BROWSER_SESSION
    try:
        from curl_cffi import requests as browser_requests
    except ImportError as exc:
        raise SourceError("this official source requires the curl_cffi browser reader") from exc
    try:
        if _BROWSER_SESSION is None:
            _BROWSER_SESSION = browser_requests.Session(impersonate="chrome")
        response = _BROWSER_SESSION.get(url, timeout=25, allow_redirects=True)
        if response.status_code in {403, 429}:
            response = browser_requests.get(url, impersonate="safari", timeout=25, allow_redirects=True)
        response.raise_for_status()
    except Exception as exc:
        raise SourceError("could not read official source {} with browser reader: {}".format(url, exc)) from exc
    final_url = str(response.url)
    if not allowed_url(final_url, allowed_domains):
        raise SourceError("redirect left official allowlist: {}".format(final_url))
    data = response.content
    if len(data) > 8_000_000:
        raise SourceError("official source is larger than the 8 MB safety limit")
    body = response.text
    return FetchResult(url, final_url, page_title(body), body, response.headers.get("Last-Modified"))


def fetch_url_with_curl(url: str, allowed_domains: Sequence[str]) -> FetchResult:
    marker = "\nRACEDAY_FINAL_URL:"
    try:
        result = subprocess.run(
            ["curl", "--fail", "--location", "--compressed", "--max-time", "25", "--max-filesize", "8000000",
             "--silent", "--show-error", "--user-agent", USER_AGENT,
             "--write-out", marker + "%{url_effective}", url],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise SourceError("could not read official source {} with verified curl: {}".format(url, detail.strip())) from exc
    if marker not in result.stdout:
        raise SourceError("verified curl did not report its final official URL")
    body, final_url = result.stdout.rsplit(marker, 1)
    final_url = final_url.strip()
    if not allowed_url(final_url, allowed_domains):
        raise SourceError("redirect left official allowlist: {}".format(final_url))
    return FetchResult(url, final_url, page_title(body), body, None)


def extract_pdf_text(data: bytes, preserve_layout: bool = False) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SourceError("PDF-uitlezer ontbreekt; installeer pypdf") from exc
    try:
        pages = [(page.extract_text(extraction_mode="layout") if preserve_layout else page.extract_text()) or "" for page in PdfReader(io.BytesIO(data)).pages]
    except Exception as exc:
        raise SourceError("officiële timetable-PDF kon niet worden uitgelezen: {}".format(exc)) from exc
    text = "\f".join(pages)
    if not text.strip():
        raise SourceError("officiële timetable-PDF bevat geen uitleesbare tekst")
    return text


def fetch_pdf_url(url: str, allowed_domains: Sequence[str], preserve_layout: bool = False) -> FetchResult:
    if not allowed_url(url, allowed_domains):
        raise SourceError("PDF-domain is not allowlisted: {}".format(url))
    url = quote(url, safe=":/?&=#%")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
    try:
        with urlopen(request, timeout=25, context=tls_context()) as response:
            final_url = response.geturl()
            if not allowed_url(final_url, allowed_domains):
                raise SourceError("PDF redirect left official allowlist: {}".format(final_url))
            data = response.read(8_000_000)
            return FetchResult(url, final_url, "Official timetable PDF", extract_pdf_text(data, preserve_layout), response.headers.get("Last-Modified"))
    except URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise SourceError("could not read official timetable PDF {}: {}".format(url, exc)) from exc
    except (OSError, HTTPException) as exc:
        raise SourceError("could not read official timetable PDF {}: {}".format(url, exc)) from exc
    marker = b"\nRACEDAY_FINAL_URL:"
    try:
        result = subprocess.run(
            ["curl", "--fail", "--location", "--max-time", "25", "--max-filesize", "8000000",
             "--silent", "--show-error", "--user-agent", USER_AGENT,
             "--write-out", marker.decode() + "%{url_effective}", url],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as curl_exc:
        detail = getattr(curl_exc, "stderr", b"")
        raise SourceError("could not read official timetable PDF: {}".format(detail.decode("utf-8", "replace").strip())) from curl_exc
    if marker not in result.stdout:
        raise SourceError("verified curl did not report the final timetable PDF URL")
    data, final_bytes = result.stdout.rsplit(marker, 1)
    final_url = final_bytes.decode("utf-8", "replace").strip()
    if not allowed_url(final_url, allowed_domains):
        raise SourceError("PDF redirect left official allowlist: {}".format(final_url))
    return FetchResult(url, final_url, "Official timetable PDF", extract_pdf_text(data, preserve_layout), None)


def fixture_result(path: Path, stable_url: str) -> FetchResult:
    body = path.read_text(encoding="utf-8")
    final_match = re.search(r'<meta\s+name="raceday-final-url"\s+content="([^"]+)"', body, re.I)
    modified = re.search(r'<meta\s+name="raceday-last-modified"\s+content="([^"]+)"', body, re.I)
    return FetchResult(
        stable_url=stable_url,
        final_url=html.unescape(final_match.group(1)) if final_match else stable_url,
        title=page_title(body),
        body=body,
        last_modified=html.unescape(modified.group(1)) if modified else None,
    )


def discover_japanese_event_url(calendar: FetchResult, event: dict, year: int, series_id: str) -> str:
    """Use season/round identity, never fuzzy circuit matching across weekends."""
    candidates = set()
    round_number = int(event.get("roundNumber") or 0)
    dates = [str(x.get("date")) for x in event.get("sessions", []) if x.get("date")]
    if series_id == "sf":
        if not any(re.fullmatch(r"{}\s+SUPER FORMULA".format(year), strip_tags(h), re.I) for h in re.findall(r"<h2\b[^>]*>(.*?)</h2>", calendar.body, re.I | re.S)):
            raise SourceError("Official Super Formula calendar has the wrong season")
        for url, label in extract_links(calendar.body, calendar.final_url):
            rounds = re.search(r"Rd\.\s*(\d+(?:\s*-\s*\d+)*)", label, re.I)
            month = re.search(r"(\d{1,2})月", label)
            if (re.search(r"/sf3/race/\d+/?$", urlparse(url).path) and rounds and month
                    and round_number in [int(n) for n in re.findall(r"\d+", rounds.group(1))]
                    and any(int(d[5:7]) == int(month.group(1)) for d in dates)):
                candidates.add(url)
    else:
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", calendar.body, re.I | re.S):
            if not re.search(r"\b{}\s+AUTOBACS\s+SUPER\s+GT\b".format(year), strip_tags(row), re.I):
                continue
            for url, label in extract_links(row, calendar.final_url):
                if re.match(r"Round\s*{}\b".format(round_number), label, re.I) and "/races/" in urlparse(url).path:
                    candidates.add(url)
    if len(candidates) != 1:
        raise SourceError("No unique official {} event for season {}, round {} and calendar dates".format(series_id, year, round_number))
    return candidates.pop()


def japanese_clock_range(value: str) -> Tuple[str, Optional[int]]:
    clocks = re.findall(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", value)
    if not clocks:
        raise SourceError("Official Japanese session has no published start time")
    try:
        start = parse_clock(clocks[0])
        end = parse_clock(clocks[1]) if len(clocks) > 1 else None
    except ValueError as exc:
        raise SourceError("Invalid Japanese session clock") from exc
    duration = None
    if end is not None:
        minutes = lambda clock: int(clock[:2]) * 60 + int(clock[3:])
        duration = minutes(end) - minutes(start)
        if duration <= 0:
            raise SourceError("Invalid official session time range")
    return start, duration


def validate_japanese_sessions(sessions: Sequence[SourceSession], event: dict) -> List[SourceSession]:
    dates = sorted({x.get("date") for x in event.get("sessions", []) if x.get("date")})
    if not dates or any(x.date < dates[0] or x.date > dates[-1] for x in sessions):
        raise SourceError("Official event dates differ from the calendar; review the event before accepting times")
    # A partially published/changed page must not look like a fully verified weekend.
    expected = {normalize_kind(x.get("name", "")) for x in event.get("sessions", [])} - {None}
    actual = {normalize_kind(x.name) for x in sessions}
    if sessions and not expected.issubset(actual):
        raise SourceError("Official timetable is incomplete for this event; keep unpublished sessions TBC")
    for kind in expected:
        if sessions and sum(normalize_kind(x.name) == kind for x in sessions) < sum(normalize_kind(x.get("name", "")) == kind for x in event.get("sessions", [])):
            raise SourceError("Official timetable is missing one or more expected sessions")
    return sorted(sessions, key=lambda x: (x.date, x.local_time, x.name))


def parse_superformula_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    title = re.search(r"(?:(20\d{2})\s+)?Rd\.\s*(\d+(?:-\d+)*)", fetch.title, re.I)
    season_matches = title and (int(title.group(1)) == year if title.group(1) else bool(re.search(r'href=["\'][^"\']*/race_taxonomy/{}["\']'.format(year), fetch.body)))
    if not season_matches or int(event.get("roundNumber") or 0) not in map(int, title.group(2).split("-")):
        raise SourceError("Super Formula page does not identify the requested season and round")
    # Only the race schedule block: results and fan schedules can contain clocks too.
    section = re.search(r'<span\b[^>]*id=[\"\']schedule[\"\'][^>]*>.*?(?=<span\b[^>]*class=[\"\']ank[\"\']|<footer|\Z)', fetch.body, re.I | re.S)
    if not section:
        return []
    body = re.split(r'<h3\b', section.group(0), flags=re.I)
    schedule = '<h3' + body[1] if len(body) > 1 else section.group(0)
    sessions, qualifying, races = [], {}, []
    for heading, rows in html_tables(schedule):
        day = re.match(r"\s*(\d{1,2})\.(\d{1,2})\b", heading)
        if not day:
            raise SourceError("Super Formula schedule table has no unambiguous date")
        try:
            session_date = date(year, int(day.group(1)), int(day.group(2))).isoformat()
        except ValueError as exc:
            raise SourceError("Invalid Super Formula schedule date") from exc
        for cells in rows:
            if len(cells) != 2:
                continue
            clock, label = cells
            practice = re.fullmatch(r"(?:FP\s*\d*\s*\(フリー走行\)|フリー走行\(FP\d+\))", label, re.I)
            qualification = re.fullmatch(r"Rd\.\s*(\d+)\s*予選\s*Q([123])(?:\s*Gr\.?\s*([AB]))?", label, re.I)
            race = re.fullmatch(r"Rd\.\s*(\d+)\s*決勝レース", label)
            if not (practice or qualification or race):
                continue
            if re.search(r"中止|延期|cancel|postpon", clock, re.I):
                continue
            start, duration = japanese_clock_range(clock)
            if practice:
                sessions.append(SourceSession("Free Practice " + str(1 + sum(x.name.startswith("Free Practice") for x in sessions)), session_date, start, source_timezone, duration))
            elif qualification:
                key = (session_date, int(qualification.group(1)))
                qualifying.setdefault(key, []).append((qualification.group(2) + (qualification.group(3) or '').upper(), start, duration))
            else:
                # A maximum race limit is not its scheduled duration.
                races.append(SourceSession("Race", session_date, start, source_timezone, duration))
    for number, ((session_date, official_round), parts) in enumerate(sorted(qualifying.items()), 1):
        part_names = {p[0] for p in parts}
        if part_names not in ({"1A", "1B", "2"}, {"1A", "1B", "2", "3"}) or len(parts) != len(part_names) or any(p[2] is None for p in parts):
            raise SourceError("Incomplete Super Formula qualifying segments")
        parts.sort(key=lambda x: x[1])
        start = parts[0][1]
        end = int(parts[-1][1][:2]) * 60 + int(parts[-1][1][3:]) + parts[-1][2]
        duration = end - (int(start[:2]) * 60 + int(start[3:]))
        name = "Qualifying" + (" " + str(number) if len(qualifying) > 1 else "")
        sessions.append(SourceSession(name, session_date, start, source_timezone, duration))
    if races and not any(normalize_kind(x.get("name", "")) == "race" for x in event.get("sessions", [])):
        raise SourceError("Official timetable lists a race missing from this calendar event; it may have been cancelled or relocated. Review before adding it.")
    for number, race in enumerate(sorted(races, key=lambda x: (x.date, x.local_time)), 1):
        name = "Race" + (" " + str(number) if len(races) > 1 else "")
        sessions.append(SourceSession(name, race.date, race.local_time, race.timezone, race.duration_minutes))
    return validate_japanese_sessions(sessions, event)


def parse_supergt_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    event_rows = [strip_tags(row) for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", fetch.body, re.I | re.S) if "大会名称" in strip_tags(row)]
    if not any(re.search(r"\b{}\s+AUTOBACS\s+SUPER\s+GT\s+Round\s*{}\b".format(year, int(event.get("roundNumber") or 0)), row, re.I) for row in event_rows):
        raise SourceError("SUPER GT page does not identify the requested season and round")
    sections = re.split(r'<div\b[^>]*class=[\"\']table_box(?:\s+hidden)?[\"\'][^>]*>', fetch.body, flags=re.I)
    dates = re.findall(r'<a\b[^>]*data-target=[\"\'](20\d{2}-\d{2}-\d{2})[\"\']', sections[0], re.I)
    if not dates and len(sections) == 1:
        return []
    if len(dates) != len(sections) - 1 or len(set(dates)) != len(dates):
        raise SourceError("SUPER GT schedule dates cannot be matched to their timetable panels")
    sessions = []
    for session_date, section in zip(dates, sections[1:]):
        try:
            if date.fromisoformat(session_date).year != year:
                raise ValueError()
        except ValueError as exc:
            raise SourceError("Invalid SUPER GT schedule date") from exc
        seen, qualifying = {}, {}
        for match in re.finditer(r'<div\b([^>]*\bclass=[\"\']race_box\s+schedule_box\s+[^\"\']*[\"\'][^>]*)>\s*<h6\b[^>]*>(.*?)</h6>', section, re.I | re.S):
            attrs, heading = match.groups()
            label = unicodedata.normalize("NFKC", strip_tags(heading))
            label_match = re.search(r"SUPER GT\s*:\s*(.+)$", label, re.I)
            if not label_match:
                continue
            name = label_match.group(1)
            if name == "公式練習":
                name = "Official Practice"
            elif name == "ウォームアップ走行":
                name = "Warmup"
            elif re.fullmatch(r"公式予選\s*\(Q[12]\)", name):
                name = "Q" + re.search(r"Q([12])", name).group(1)
            elif re.fullmatch(r"決勝レース(?:\s*\([^)]*\))?", name):
                name = "Race"
            else:
                continue
            start_attr = re.search(r'\bdata-from=[\"\']([^\"\']*)[\"\']', attrs)
            end_attr = re.search(r'\bdata-to=[\"\']([^\"\']*)[\"\']', attrs)
            if not start_attr:
                raise SourceError("Missing SUPER GT session start")
            clock = start_attr.group(1) + "-" + (end_attr.group(1) if end_attr else "")
            start, duration = japanese_clock_range(clock)
            visible_start, visible_duration = japanese_clock_range(label.split("SUPER GT")[0])
            if start != visible_start or (visible_duration is not None and duration != visible_duration):
                raise SourceError("SUPER GT displayed and embedded session times disagree")
            # Race bars may have a layout-only end absent from the visible timetable.
            duration = visible_duration
            value = (start, duration)
            if name in seen and seen[name] != value:
                raise SourceError("Conflicting desktop/mobile SUPER GT times")
            if name in seen:
                continue
            seen[name] = value
            if name in {"Q1", "Q2"}:
                qualifying[name] = value
            else:
                sessions.append(SourceSession(name, session_date, start, source_timezone, duration))
        if qualifying:
            if set(qualifying) != {"Q1", "Q2"} or any(v[1] is None for v in qualifying.values()):
                raise SourceError("Incomplete SUPER GT qualifying segments")
            start, _ = qualifying['Q1']
            last, duration = qualifying['Q2']
            minutes = lambda clock: int(clock[:2]) * 60 + int(clock[3:])
            if minutes(last) <= minutes(start):
                raise SourceError("Invalid SUPER GT qualifying order")
            sessions.append(SourceSession("Qualifying", session_date, start, source_timezone, minutes(last) + duration - minutes(start)))
    races = [x for x in sessions if x.name == "Race"]
    expected_race_dates = {x.get("date") for x in event.get("sessions", []) if normalize_kind(x.get("name", "")) == "race"}
    if len(races) > 1 or any(x.date not in expected_race_dates for x in races):
        raise SourceError("Shared or moved SUPER GT race timetable: review which race belongs to this round")
    return validate_japanese_sessions(sessions, event)


def academy_page_data(fetch: FetchResult) -> dict:
    match = re.search(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', fetch.body, re.I | re.S)
    try:
        data = json.loads(match.group(1))["props"]["pageProps"]["pageData"] if match else None
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError("Could not decode the official F1 Academy schedule") from exc
    if not isinstance(data, dict):
        raise SourceError("Official F1 Academy schedule data is unavailable")
    return data


def academy_event_record(data: dict, event: dict, year: int) -> dict:
    if not re.fullmatch(r"{} F1 Academy".format(year), str(data.get("SeasonName", "")), re.I):
        raise SourceError("F1 Academy source season does not match this calendar")
    records = data.get("Races") if "Races" in data else [data]
    if not isinstance(records, list):
        raise SourceError("Invalid F1 Academy race list")
    matches = [r for r in records if isinstance(r, dict) and r.get("RoundNumber") == event.get("roundNumber")]
    if len(matches) != 1:
        raise SourceError("No unique official F1 Academy round matches this event")
    race = matches[0]
    circuit = race.get("CircuitName") or (race.get("CircuitInformation") or {}).get("CircuitName")
    aliases = {"las vegas street circuit": "las vegas strip circuit"}
    wanted = normalize(event.get("circuitName"))
    if not wanted or aliases.get(wanted, wanted) != normalize(circuit):
        raise SourceError("Official F1 Academy circuit does not match this event")
    try:
        first, last = date.fromisoformat(race["RaceStartDate"]), date.fromisoformat(race["RaceEndDate"])
        dates = [date.fromisoformat(x["date"]) for x in event.get("sessions", []) if x.get("date")]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError("Invalid F1 Academy weekend dates") from exc
    if first.year != year or last < first or (last - first).days > 7 or not dates or max(dates) < first or min(dates) > last + timedelta(days=1):
        raise SourceError("F1 Academy weekend dates do not match this event")
    return race


def discover_academy_event_url(calendar: FetchResult, event: dict, year: int) -> str:
    race = academy_event_record(academy_page_data(calendar), event, year)
    race_id = race.get("RaceId")
    if not isinstance(race_id, int) or race_id <= 0:
        raise SourceError("Official F1 Academy round has no valid race ID")
    # Follow only the URL whose ID belongs to the exact official season/round.
    candidates = {url for url, _ in extract_links(calendar.body, calendar.final_url)
                  if urlparse(url).path == "/Racing-Series/Results" and
                  urlparse(url).query == "raceid={}".format(race_id)}
    if len(candidates) != 1:
        raise SourceError("Official F1 Academy event link is missing or ambiguous")
    return candidates.pop()


def parse_academy_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    race = academy_event_record(academy_page_data(fetch), event, year)
    records = race.get("SessionResults", race.get("Sessions"))
    if not isinstance(records, list):
        raise SourceError("Official F1 Academy sessions are unavailable")
    labels = {
        "Free Practice": "PRACTICE", "Practice": "PRACTICE",
        "Qualifying": "QUALIFYING", "Qualifying 1": "QUALIFYING", "Qualifying 2": "QUALIFYING",
        "Opening Race": "RESULT", "Reverse Grid Race": "RESULT", "Feature Race": "RESULT",
    }
    sessions, seen = [], {}
    for item in records:
        if not isinstance(item, dict):
            raise SourceError("Invalid official F1 Academy session record")
        name = item.get("SessionName")
        if name not in labels or item.get("SessionType", item.get("SessionCode")) != labels[name]:
            continue
        # TBC entries still contain placeholder timestamps, sometimes even a
        # stale summer offset. They are never evidence of a published time.
        if item.get("Unconfirmed") is not False:
            continue
        try:
            start = datetime.fromisoformat(item["SessionStartTime"])
            end = datetime.fromisoformat(item["SessionEndTime"]) if item.get("SessionEndTime") else None
            zone = ZoneInfo(source_timezone)
        except (ValueError, KeyError, TypeError, ZoneInfoNotFoundError) as exc:
            raise SourceError("Invalid confirmed F1 Academy session timestamp") from exc
        if start.tzinfo is None or (end is not None and end.tzinfo is None):
            raise SourceError("Confirmed F1 Academy session is missing its official UTC offset")
        local = start.astimezone(zone)
        if local.replace(tzinfo=None) != start.replace(tzinfo=None):
            raise SourceError("Official F1 Academy UTC offset disagrees with the circuit's IANA timezone")
        if start.second or start.microsecond or (end is not None and (end.second or end.microsecond)):
            raise SourceError("Official F1 Academy session has unsupported sub-minute precision")
        if not race["RaceStartDate"] <= local.date().isoformat() <= race["RaceEndDate"]:
            raise SourceError("F1 Academy session falls outside its official weekend")
        duration = None
        if end is not None:
            if end.astimezone(zone).replace(tzinfo=None) != end.replace(tzinfo=None):
                raise SourceError("F1 Academy session end offset disagrees with the circuit timezone")
            duration = int((end - start).total_seconds() / 60)
            if not 0 < duration <= 360:
                raise SourceError("Invalid confirmed F1 Academy session duration")
        session = SourceSession(name, local.date().isoformat(), local.strftime("%H:%M"), source_timezone,
                                duration, start.astimezone(timezone.utc).strftime("%H:%M"))
        if name in seen and seen[name] != session:
            raise SourceError("Conflicting F1 Academy session records")
        if name not in seen:
            seen[name] = session
            sessions.append(session)
    if not sessions and any(isinstance(x, dict) and x.get("SessionName") in labels and x.get("Unconfirmed") is not False for x in records):
        raise SourceError("F1 Academy has not confirmed the session times for this round; internal placeholder timestamps must remain TBC")
    # Montreal publishes two grid classifications for the same physical
    # qualifying session. Preserve one calendar session if both intervals agree;
    # distinct qualifying intervals still remain separate.
    q1, q2 = seen.get("Qualifying 1"), seen.get("Qualifying 2")
    if q1 and q2 and (q1.date, q1.local_time, q1.duration_minutes, q1.utc_hint) == (q2.date, q2.local_time, q2.duration_minutes, q2.utc_hint):
        sessions = [x for x in sessions if x.name not in {"Qualifying 1", "Qualifying 2"}]
        sessions.append(SourceSession("Qualifying", q1.date, q1.local_time, q1.timezone, q1.duration_minutes, q1.utc_hint))
    return sorted(sessions, key=lambda x: (x.date, x.local_time, x.name))


def match_academy_session(source: SourceSession, official_sessions: Sequence[SourceSession], available: Sequence[dict], all_existing: Optional[Sequence[dict]] = None) -> Tuple[Optional[dict], Optional[str]]:
    exact = [x for x in available if normalize(x.get("name")) == normalize(source.name)]
    if len(exact) == 1:
        return exact[0], None
    race_names = {"Opening Race", "Reverse Grid Race", "Feature Race"}
    if source.name in race_names:
        races = sorted([x for x in official_sessions if x.name in race_names], key=lambda x: (x.date, x.local_time))
        existing_races = [x for x in (all_existing if all_existing is not None else available) if normalize_kind(x.get("name", "")) in {"race", "featureRace"}]
        if len(races) != len(existing_races):
            return None, "The confirmed F1 Academy race count differs from the calendar; review legacy race numbering before matching."
        number = races.index(source) + 1
        legacy = [x for x in available if re.fullmatch(r"race {}".format(number), normalize(x.get("name")))]
        if len(legacy) == 1:
            return legacy[0], None
        if exact or legacy:
            return None, "F1 Academy race slot is ambiguous"
        # Never map an Opening Race to an already named Reverse Grid/Feature Race.
        return None, None
    return match_session(source, available)


def discover_event_url(calendar: FetchResult, event: dict, year: int) -> Optional[str]:
    target = "{} {} {} {}".format(
        event.get("raceName", ""), event.get("circuitName", ""), event.get("city", ""), year
    )
    best: Tuple[float, Optional[str]] = (0.0, None)
    for link in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", calendar.body, re.I | re.S):
        url = urljoin(calendar.final_url, html.unescape(link.group(1)))
        if "imsa.com" in (urlparse(url).hostname or "") and "/events/" in urlparse(url).path.lower() and not urlparse(url).path.endswith("/"):
            url = url + "/"
        label = strip_tags(link.group(2))
        if not re.search(r"event|racing|race|schedule|timetable|programme|document|results", "{} {}".format(url, label), re.I):
            continue
        nearby = strip_tags(calendar.body[max(0, link.start() - 700):link.end()])
        direct_score = similarity(target, "{} {}".format(label, url.replace("-", " ")))
        target_tokens, nearby_tokens = tokens(target), tokens(nearby)
        context_score = len(target_tokens & nearby_tokens) / len(target_tokens) if target_tokens else 0.0
        score = max(direct_score, context_score * 0.8)
        if "nascar.com" in (urlparse(url).hostname or "") and "/weekend-schedule/" in url:
            score += 0.35
        if "imsa.com" in (urlparse(url).hostname or ""):
            if "/events/" in urlparse(url).path.lower():
                score += 0.5
            elif "/news/" in urlparse(url).path.lower():
                score -= 0.5
        if score > best[0]:
            best = (score, url)
    return best[1] if best[0] >= 0.14 else None


def discover_formula1_event_url(calendar: FetchResult, event: dict, year: int) -> Optional[str]:
    """Select the official F1 race page by round, excluding testing pages."""
    round_number = event.get("roundNumber")
    if not isinstance(round_number, int):
        return None
    candidates = []
    expected_path = "/en/racing/{}/".format(year)
    for url, label in extract_links(calendar.body, calendar.final_url):
        path = urlparse(url).path.rstrip("/")
        if expected_path not in path + "/" or "pre-season-testing" in path:
            continue
        if re.search(r"\bround\s+{}\b".format(round_number), normalize(label)):
            candidates.append(url)
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else None


def discover_btcc_event_url(calendar: FetchResult, event: dict) -> Optional[str]:
    """Select the official BTCC circuit page by weekend order."""
    links = []
    for url, _label in extract_links(calendar.body, calendar.final_url):
        if "/circuit/" not in urlparse(url).path.lower():
            continue
        if url not in links:
            links.append(url)
    round_number = event.get("roundNumber")
    if isinstance(round_number, int) and 1 <= round_number <= len(links):
        return links[round_number - 1]
    return None


def discover_timetable_pdf(event_page: FetchResult) -> Optional[str]:
    candidates = []
    for url, label in extract_links(event_page.body, event_page.final_url):
        combined = "{} {}".format(label, url)
        is_document = urlparse(url).path.lower().endswith(".pdf") or "/document/download/" in url or "pdf" in label.lower()
        if is_document and re.search(r"timetable|schedule|programme|itinerary", combined, re.I):
            if re.search(r"weekend.?schedule", combined, re.I):
                priority = 5
            elif re.search(r"\bofficial\b", combined, re.I):
                priority = 4
            elif re.search(r"timetable", combined, re.I):
                priority = 3
            elif re.search(r"\bprovisional\b", combined, re.I):
                priority = 1
            else:
                priority = 2
            candidates.append((priority, url))
    return max(candidates, default=(0, None))[1]


def discover_rally_itinerary_url(event_page: FetchResult) -> Optional[str]:
    """Find the official stage itinerary linked from a WRC/ERC event page."""
    candidates = []
    for url, label in extract_links(event_page.body, event_page.final_url):
        combined = "{} {}".format(label, url)
        is_pdf = urlparse(url).path.lower().endswith(".pdf") or "pdf" in label.lower()
        # WRC's official Chile filename currently contains the typo
        # "Itinrerary". The visible Download PDF label and itinerary-page
        # context are therefore also authoritative signals.
        is_itinerary = bool(re.search(r"itinerar|itinrerar", combined, re.I))
        if is_pdf and re.search(r"download\s+pdf", label, re.I) and "itinerary" in event_page.final_url.lower():
            is_itinerary = True
        if not is_itinerary:
            continue
        if url.rstrip("/") == event_page.final_url.rstrip("/"):
            continue
        # Prefer the HTML itinerary tab when both it and a PDF are linked.
        # The HTML carries the same official stage table, needs no optional
        # PDF dependency in GitHub Actions, and remains useful when no PDF has
        # been published yet.
        if not is_pdf and re.search(r"itinerar", combined, re.I):
            priority = 5
        elif is_pdf:
            priority = 4
        else:
            priority = 2
        candidates.append((priority, url))
    return max(candidates, default=(0, None))[1]


def rally_page_ready(fetch: FetchResult) -> bool:
    """Check for the useful WRC/ERC payload, not merely a large app shell."""
    path = urlparse(fetch.final_url).path.lower()
    has_timed_stages = any(
        re.match(r"^\d{1,2}:\d{2}\s*:?\s*(?:shakedown\b|(?:wolf\s+)?power\s+stage\b|sss?\s*\d+\b)", line, re.I)
        for line in document_lines(fetch.body)
    )
    if has_timed_stages:
        return True
    if "itinerary" in path or "/v3/api/graphql/" in path:
        return False
    if path.rstrip("/").endswith("/calendar"):
        return len(re.findall(r"/events/", fetch.body, re.I)) >= 2
    if "/events/" in path:
        return discover_rally_itinerary_url(fetch) is not None
    return len(fetch.body) >= 20_000


def rally_content_api_url(itinerary_url: str) -> Optional[str]:
    """Build WRC Promoter's official JSON endpoint for an itinerary tab."""
    parsed = urlparse(itinerary_url)
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if "itinerar" not in slug.lower():
        return None
    return (
        "{}://{}/v3/api/graphql/v1/v3/feed/en-INT?disableUsageRestrictions=true"
        "&filter%5Btype%5D=event-details&filter%5BuriSlug%5D={}"
        "&page%5Blimit%5D=1&rb3Locale=en&rb3Schema=v1:inlineContent"
    ).format(parsed.scheme, parsed.netloc, quote(slug, safe=""))


def retry_rally_prerender(fetch: FetchResult, url: str, allowed_domains: Sequence[str]) -> FetchResult:
    """WRC Promoter may return a full-size shell before useful data is ready."""
    result = fetch
    api_url = rally_content_api_url(url)
    if not rally_page_ready(result) and api_url:
        try:
            api_result = fetch_url(api_url, allowed_domains)
            if rally_page_ready(api_result):
                # Keep the public itinerary URL as the reviewable citation.
                return FetchResult(
                    fetch.stable_url, fetch.final_url, fetch.title,
                    api_result.body, api_result.last_modified,
                )
        except SourceError as exc:
            LOG.debug("Official rally itinerary API fallback failed: %s", exc)
    # WRC's edge cache can return several complete-looking app shells before
    # returning the server-rendered itinerary. Allow enough bounded retries
    # for the useful HTML while still failing cleanly for future rallies whose
    # itinerary has not been published.
    for _ in range(5):
        if rally_page_ready(result):
            break
        candidate = fetch_url(url, allowed_domains)
        if rally_page_ready(candidate) or len(candidate.body) > len(result.body):
            result = candidate
    return result


def discover_event_calendar(event_page: FetchResult) -> Optional[str]:
    for url, label in extract_links(event_page.body, event_page.final_url):
        combined = "{} {}".format(label, url)
        if "/race/calendar/" in urlparse(url).path.lower() or re.search(r"add to (?:my )?calendar", combined, re.I):
            return url
    return None


def html_tables(body: str) -> Iterable[Tuple[str, List[List[str]]]]:
    """Yield (nearest heading, cells) for ordinary official schedule tables."""
    for table_match in re.finditer(r"<table\b[^>]*>(.*?)</table>", body, re.I | re.S):
        prefix = body[max(0, table_match.start() - 1200):table_match.start()]
        headings = re.findall(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", prefix, re.I | re.S)
        captions = re.findall(r"<caption\b[^>]*>(.*?)</caption>", table_match.group(1), re.I | re.S)
        heading = strip_tags(captions[-1]) if captions else (strip_tags(headings[-1]) if headings else "")
        rows: List[List[str]] = []
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_match.group(1), re.I | re.S):
            cells = [strip_tags(cell) for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.I | re.S)]
            if cells:
                rows.append(cells)
        if rows:
            yield heading, rows


def normalize_kind(name: str) -> Optional[str]:
    raw = normalize(name)
    if any(word in raw for word in IGNORED_SESSION_WORDS):
        return None
    # SRO endurance schedules use pre-qualifying as the second practice-style
    # running session. It is not one of the driver qualifying sessions.
    if "pre qualifying" in raw:
        return "practice"
    if "sprint qualifying" in raw or "sprint shootout" in raw:
        return "sprintQualifying"
    if raw == "qualifying race":
        return "sprintRace"
    if raw == "sprint" or ("sprint" in raw and "race" in raw):
        return "sprintRace"
    if "feature" in raw and "race" in raw:
        return "featureRace"
    if "hyperpole" in raw:
        return "hyperpole"
    if raw == "superpole":
        return "qualifying"
    if "shakedown" in raw:
        return "shakedown"
    if re.match(r"^(?:sss?|special stage) ?\d+\b", raw):
        return "stage"
    if "qualif" in raw or "shootout" in raw:
        return "qualifying"
    if "practice" in raw or "warm up" in raw or "warmup" in raw:
        return "practice"
    if "test" in raw:
        return "testing"
    if re.search(r"\b(race|duel)\b", raw):
        return "race"
    return None


def parse_official_tables(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    sessions: List[SourceSession] = []
    for heading, rows in html_tables(fetch.body):
        session_date = parse_heading_date(heading, year)
        if not session_date or len(rows) < 2:
            continue
        headers = [normalize(cell) for cell in rows[0]]
        session_col = next((i for i, value in enumerate(headers) if "session" in value), 0)
        gmt_col = next((i for i, value in enumerate(headers) if value in {"gmt", "utc"} or "gmt" in value), None)
        time_candidates = [i for i, value in enumerate(headers) if "time" in value or "eastern" in value or value in {"et", "ct", "local"}]
        explicit_local = [i for i in time_candidates if "local" in headers[i] or "track" in headers[i] or "eastern" in headers[i] or headers[i] in {"et", "ct"}]
        generic_time = [i for i in time_candidates if headers[i] in {"time", "start time", "start"}]
        local_col = next(iter(explicit_local or generic_time), None)
        # Browser-local/My Time and UTC columns cannot be interpreted as track time.
        for cells in rows[1:]:
            if session_col >= len(cells) or local_col is None or local_col >= len(cells):
                continue
            name = cells[session_col]
            kind = normalize_kind(name)
            if not kind:
                continue
            try:
                local_clock = parse_clock(cells[local_col])
            except ValueError:
                continue
            utc_hint = None
            if gmt_col is not None and gmt_col < len(cells):
                try:
                    utc_hint = parse_clock(cells[gmt_col])
                except ValueError:
                    pass
            duration = None
            # Some official tables include either a duration cell or a start-end range.
            for cell in cells:
                duration_match = re.search(r"\b(\d{1,3})\s*(?:min|minutes|')\b", cell, re.I)
                if duration_match:
                    duration = int(duration_match.group(1))
                    break
            sessions.append(SourceSession(name, session_date, local_clock, source_timezone, duration, utc_hint))
    return sessions


def parse_btcc_timetable(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse only BTCC championship rows from an official weekend timetable."""
    sessions: List[SourceSession] = []
    race_number = 0
    race_starts = set()
    for heading, rows in html_tables(fetch.body):
        session_date = parse_document_date(heading, year)
        if not session_date or len(rows) < 2:
            continue
        headers = [normalize(cell) for cell in rows[0]]
        time_col = next((index for index, value in enumerate(headers) if value == "time"), None)
        activity_col = next((index for index, value in enumerate(headers) if value == "activity"), None)
        championship_col = next((index for index, value in enumerate(headers) if value == "championship"), None)
        if time_col is None or activity_col is None:
            continue
        for cells in rows[1:]:
            if max(time_col, activity_col) >= len(cells):
                continue
            championship = cells[championship_col] if championship_col is not None and championship_col < len(cells) else cells[activity_col]
            if "british touring car championship" not in normalize(championship):
                continue
            activity = cells[activity_col] if championship_col is not None else "Race"
            normalized_activity = normalize(activity)
            if normalized_activity == "free practice":
                name = "Free Practice"
            elif normalized_activity == "qualifying":
                name = "Qualifying 1"
            elif normalized_activity == "qualifying race":
                name = "Qualifying Race"
            elif normalized_activity == "race":
                race_key = (session_date, normalize(cells[time_col]))
                if race_key in race_starts:
                    continue
                race_starts.add(race_key)
                race_number += 1
                name = "Race {}".format(race_number)
            else:
                continue
            clocks = re.findall(r"\b\d{1,2}:\d{2}\b", cells[time_col])
            if not clocks:
                continue
            local_clock = parse_clock(clocks[0])
            duration = None
            if len(clocks) >= 2:
                start_hour, start_minute = map(int, parse_clock(clocks[0]).split(":"))
                end_hour, end_minute = map(int, parse_clock(clocks[1]).split(":"))
                duration = (end_hour * 60 + end_minute) - (start_hour * 60 + start_minute)
                if duration <= 0:
                    duration += 24 * 60
            sessions.append(SourceSession(name, session_date, local_clock, source_timezone, duration))
    unique = {(item.name, item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


def parse_supercars_schedule(fetch: FetchResult, year: int, source_timezone: str, series_name: str) -> List[SourceSession]:
    """Parse the official Supercars schedule and exclude every support category.

    Supercars event pages embed Contentful race-session records in the Next.js
    response. Each record carries an exact series name and an offset-aware start
    instant, which is safer than interpreting the browser's "My Time" display.
    Official news articles use ordinary tables, so those are supported as a
    fallback with the same exact category filter.
    """
    track_zone = ZoneInfo(source_timezone)
    expected_series = normalize(series_name)
    sessions: List[SourceSession] = []
    # Decode the Next.js strings before reading complete JSON objects. Field
    # order and logo presence are not record boundaries.
    chunks = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"self\.__next_f\.push\(", fetch.body):
        try:
            value, _ = decoder.raw_decode(fetch.body[match.end():])
            if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
                chunks.append(value[1])
        except ValueError:
            continue
    decoded = "".join(chunks) if chunks else html.unescape(fetch.body)
    records = []
    for match in re.finditer(r'"raceSessionsCollection"\s*:\s*', decoded):
        try:
            collection, _ = decoder.raw_decode(decoded[match.end():])
            records.extend(collection.get("items", []))
        except (ValueError, AttributeError):
            continue
    for fields in records:
        if not isinstance(fields, dict) or normalize((fields.get("series") or {}).get("name", "")) != expected_series:
            continue
        name = re.sub(r"\bTTSO\b", "Top Ten Shootout", fields.get("name", ""))
        if not fields.get("startDate") or not normalize_kind(name):
            continue
        try:
            start = datetime.fromisoformat(fields["startDate"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(fields.get("endDate", "").replace("Z", "+00:00")) if fields.get("endDate") else None
        except ValueError:
            continue
        if start.tzinfo is None:
            continue
        local_start = start.astimezone(track_zone)
        if local_start.year != year:
            continue
        duration = int((end - start).total_seconds() // 60) if end and end.tzinfo else None
        sessions.append(SourceSession(
            name, local_start.date().isoformat(), local_start.strftime("%H:%M"),
            source_timezone, duration if duration and duration > 0 else None,
            start.astimezone(timezone.utc).strftime("%H:%M"),
        ))

    if not sessions:
        for heading, rows in html_tables(fetch.body):
            session_date = parse_document_date(heading, year)
            if not session_date or len(rows) < 2:
                continue
            headers = [normalize(cell) for cell in rows[0]]
            category_col = next((i for i, value in enumerate(headers) if "category" in value or "series" in value), None)
            session_col = next((i for i, value in enumerate(headers) if "session" in value), None)
            start_col = next((i for i, value in enumerate(headers) if value == "start" or "start time" in value), None)
            finish_col = next((i for i, value in enumerate(headers) if value in {"finish", "end"} or "finish time" in value), None)
            if category_col is None or session_col is None or start_col is None:
                continue
            for cells in rows[1:]:
                if max(category_col, session_col, start_col) >= len(cells):
                    continue
                if normalize(cells[category_col]) not in {expected_series, "supercars"}:
                    continue
                name = cells[session_col]
                if not normalize_kind(name):
                    continue
                try:
                    start_clock = parse_clock(cells[start_col])
                except ValueError:
                    continue
                duration = None
                if finish_col is not None and finish_col < len(cells):
                    try:
                        start_value = datetime.strptime(start_clock, "%H:%M")
                        end_value = datetime.strptime(parse_clock(cells[finish_col]), "%H:%M")
                        minutes = int((end_value - start_value).total_seconds() // 60)
                        duration = minutes if minutes > 0 else minutes + 24 * 60
                    except ValueError:
                        pass
                sessions.append(SourceSession(name, session_date, start_clock, source_timezone, duration))

    unique = {(normalize(item.name), item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, normalize(item.name)))


class _NascarNode:
    def __init__(self, tag: str = "document", attrs: Optional[dict] = None, parent: Optional["_NascarNode"] = None):
        self.tag, self.attrs, self.parent = tag, attrs or {}, parent
        self.parts: List[str] = []
        self.children: List["_NascarNode"] = []

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class _NascarHTMLTree(HTMLParser):
    """Small tolerant tree used only for NASCAR's server-rendered schedule cards."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _NascarNode()
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        node = _NascarNode(tag.lower(), values, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() == "img" and values.get("alt"):
            node.parts.append(values["alt"])
        if tag.lower() not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data.strip() and self.stack[-1].tag not in {"script", "style"}:
            self.stack[-1].parts.append(data)

    def close(self) -> None:
        super().close()

        def aggregate(node: _NascarNode) -> List[str]:
            parts = list(node.parts)
            for child in node.children:
                parts.extend(aggregate(child))
            node.parts = parts
            return parts

        aggregate(self.root)


NASCAR_SERIES_MARKERS = {
    "nascar": ("nascar cup series", "nascar-cup-series"),
    "nascar_oreilly": ("nascar oreilly auto parts series", "nascar-oreilly-auto-parts-series", "noaps"),
    "nascar_trucks": ("nascar craftsman truck series", "nascar-craftsman-truck-series", "ncts"),
}

NASCAR_FEED_SERIES = {
    "nascar": "series_1",
    "nascar_oreilly": "series_2",
    "nascar_trucks": "series_3",
}


def _nascar_date(value: str, year: int) -> Optional[str]:
    raw = html.unescape(value)
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})(?:[T\s]|\b)", raw)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat()
        except ValueError:
            return None
    # The live weekend table stores its authoritative date on every session
    # row as data-date="MM/DD/YYYY". It is not rendered as text inside the row,
    # so this must be handled before looking for a written month name.
    us_numeric = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", raw)
    if us_numeric:
        try:
            return date(
                int(us_numeric.group(3)),
                int(us_numeric.group(1)),
                int(us_numeric.group(2)),
            ).isoformat()
        except ValueError:
            return None
    month_first = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2})(?:,?\s+(20\d{2}))?\b", raw, re.I,
    )
    if not month_first:
        return None
    try:
        return date(int(month_first.group(3) or year), MONTHS[month_first.group(1).lower()], int(month_first.group(2))).isoformat()
    except ValueError:
        return None


def _nascar_node_date(node: _NascarNode, year: int) -> Optional[str]:
    current: Optional[_NascarNode] = node
    while current:
        candidates = set()
        for value in list(current.attrs.values()) + [current.text()]:
            parsed = _nascar_date(value, year)
            if parsed:
                candidates.add(parsed)
        if len(candidates) == 1:
            return next(iter(candidates))
        current = current.parent
    return None


def parse_nascar_text(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse NASCAR's combined weekend grid, including all three national series."""
    parser = _NascarHTMLTree()
    parser.feed(fetch.body)
    parser.close()
    markers = NASCAR_SERIES_MARKERS.get(event.get("seriesId"), ())
    target_race = normalize(event.get("raceName", ""))
    target_track = normalize(event.get("circuitName", ""))
    sessions: List[SourceSession] = []

    def visit(node: _NascarNode) -> None:
        raw = node.text()
        normalized = normalize(raw)
        normalized_markers = {normalize(marker) for marker in markers}
        marker_count = max((normalized.count(marker) for marker in normalized_markers), default=0)
        # A schedule card contains one series logo. Day columns and page wrappers
        # contain several, so excluding them prevents cross-card time matches.
        if marker_count == 1 and (not target_track or target_track in normalized):
            time_match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:ET|EST|EDT)\b", raw, re.I)
            session_date = _nascar_node_date(node, year)
            if time_match and session_date:
                if re.search(r"\bqualif(?:y|ying|ication)\b", normalized):
                    name = "Qualifying"
                elif re.search(r"\b(?:final\s+)?practice\b", normalized):
                    name = "Final Practice" if "final practice" in normalized else "Practice"
                elif target_race and target_race in normalized:
                    name = "Race"
                else:
                    name = ""
                if name:
                    sessions.append(SourceSession(name, session_date, parse_clock(time_match.group()), source_timezone))
        for child in node.children:
            visit(child)

    visit(parser.root)
    if sessions:
        unique = {(normalize(item.name), item.date, item.local_time): item for item in sessions}
        return sorted(unique.values(), key=lambda item: (item.date, item.local_time, normalize(item.name)))

    # Compatibility with the former article-like page, which published one race
    # date and time together around the event name.
    text = strip_tags(fetch.body)
    needle = next((term for term in (event.get("raceName", ""), event.get("circuitName", "")) if term and re.search(re.escape(term), text, re.I)), None)
    window = text
    if needle:
        match = re.search(re.escape(needle), text, re.I)
        window = text[max(0, match.start() - 400):match.end() + 700]
    session_date = _nascar_date(window, year)
    time_match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:ET|EST|EDT)\b", window, re.I)
    return [SourceSession("Race", session_date, parse_clock(time_match.group()), source_timezone)] if session_date and time_match else []


def parse_nascar_feed(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse NASCAR's public schedule cache when nascar.com blocks automation."""
    try:
        payload = json.loads(fetch.body)
    except json.JSONDecodeError as exc:
        raise SourceError("official NASCAR schedule feed returned invalid JSON") from exc

    feed_key = NASCAR_FEED_SERIES.get(event.get("seriesId"))
    races = payload.get(feed_key, []) if feed_key else []
    target_name = normalize(event.get("raceName"))
    target_track = normalize(event.get("circuitName"))
    event_dates = []
    for session in event.get("sessions", []):
        try:
            event_dates.append(date.fromisoformat(str(session.get("date"))))
        except ValueError:
            pass

    candidates = []
    for race in races:
        if int(race.get("race_season") or 0) != year:
            continue
        race_track = normalize(race.get("track_name"))
        if target_track and target_track not in race_track and race_track not in target_track:
            continue
        race_name = normalize(race.get("race_name"))
        name_score = similarity(target_name, race_name)
        if target_name and (target_name in race_name or race_name in target_name):
            name_score += 2.0
        try:
            race_day = date.fromisoformat(str(race.get("date_scheduled", ""))[:10])
            distance = min((abs((race_day - item).days) for item in event_dates), default=366)
        except ValueError:
            distance = 366
        candidates.append((name_score, -distance, race))

    if not candidates:
        return []
    race = max(candidates, key=lambda item: (item[0], item[1]))[2]
    sessions = []
    canonical_names = {1: "Practice", 2: "Qualifying", 3: "Race"}
    for entry in race.get("schedule", []):
        run_type = int(entry.get("run_type") or 0)
        if run_type not in canonical_names:
            continue
        raw_instant = str(entry.get("start_time_utc") or "").strip()
        if not raw_instant:
            continue
        try:
            instant = datetime.fromisoformat(raw_instant.replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
            instant = instant.astimezone(timezone.utc)
            local = instant.astimezone(ZoneInfo(source_timezone))
        except (ValueError, ZoneInfoNotFoundError):
            continue
        sessions.append(SourceSession(
            canonical_names[run_type],
            local.date().isoformat(),
            local.strftime("%H:%M"),
            source_timezone,
            utc_hint=instant.strftime("%H:%M"),
        ))
    unique = {(item.name, item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


def parse_british_gt_pdf(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    sessions: List[SourceSession] = []
    for page_index, page in enumerate(fetch.body.split("\f")):
        date_match = re.search(
            r"\b(\d{1,2})\s*/\s*(\d{1,2})\s+"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
            page, re.I,
        )
        if not date_match or int(date_match.group(4)) != year:
            continue
        days = [int(date_match.group(1)), int(date_match.group(2))]
        day = days[min(page_index, len(days) - 1)]
        session_date = date(year, MONTHS[date_match.group(3).lower()], day).isoformat()
        for line in page.splitlines():
            match = re.search(r"\bBritish GT Championship\s+\d+\s+(.+)$", line, re.I)
            if not match:
                continue
            remainder = match.group(1).strip()
            times = list(re.finditer(r"\b\d{2}:\d{2}\b", remainder))
            if len(times) < 4:
                continue
            name = remainder[:times[0].start()].strip()
            if re.fullmatch(r"Race\s+\d+", name, re.I):
                name = "Race"
            kind = normalize_kind(name)
            if not kind:
                continue
            duration_hours, duration_minutes = map(int, times[0].group().split(":"))
            duration = duration_hours * 60 + duration_minutes
            sessions.append(SourceSession(name, session_date, times[2].group(), source_timezone, duration))
    return sessions


def document_lines(body: str) -> List[str]:
    if body.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            pass
        else:
            values: List[str] = []

            def collect_strings(value: object) -> None:
                if isinstance(value, str):
                    values.extend(value.splitlines())
                elif isinstance(value, list):
                    for item in value:
                        collect_strings(item)
                elif isinstance(value, dict):
                    for item in value.values():
                        collect_strings(item)

            collect_strings(payload)
            return [" ".join(line.split()) for line in values if line.strip()]
    if re.search(r"<html|<body|<div|<table|<li|<h[1-6]", body, re.I):
        body = re.sub(r"</(?:div|p|li|tr|h[1-6]|section|article)>|<br\s*/?>", "\n", body, flags=re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        body = html.unescape(body)
    return [" ".join(line.split()) for line in body.splitlines() if line.strip()]


def parse_document_date(line: str, year: int) -> Optional[str]:
    month_names = "January|February|March|April|May|June|July|August|September|October|November|December"
    month_first = re.search(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*(%s)\s+(\d{1,2})(?:[,]?\s+(20\d{2}))?\b" % month_names, line, re.I)
    day_first = re.search(r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*(\d{1,2})\s+(%s)(?:\s+(20\d{2}))?\b" % month_names, line, re.I)
    numeric = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[,]?\s+(\d{1,2})/(\d{1,2})(?:/(20\d{2}))?\b", line, re.I)
    dotted = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[,]?\s+(\d{1,2})[.-](\d{1,2})[.-](20\d{2})\b", line, re.I)
    if month_first:
        return date(int(month_first.group(3) or year), MONTHS[month_first.group(1).lower()], int(month_first.group(2))).isoformat()
    if day_first:
        return date(int(day_first.group(3) or year), MONTHS[day_first.group(2).lower()], int(day_first.group(1))).isoformat()
    if numeric:
        return date(int(numeric.group(3) or year), int(numeric.group(1)), int(numeric.group(2))).isoformat()
    if dotted:
        return date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1))).isoformat()
    return None


def parse_rally_itinerary(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse only shakedown and timed SS/SSS rows from an official rally itinerary."""
    sessions: List[SourceSession] = []
    current_date: Optional[str] = None
    for line in document_lines(fetch.body):
        parsed_date = parse_document_date(line, year)
        if parsed_date:
            current_date = parsed_date
        if not current_date:
            continue
        clock_match = re.match(r"^(\d{1,2}:\d{2})\s*:?\s*(.+)$", line)
        if not clock_match:
            continue
        clock, activity = clock_match.groups()
        activity = re.sub(r"\s*\([^)]*\bkm\)\s*$", "", activity, flags=re.I).strip(" :-")
        if re.match(r"^shakedown\b", activity, re.I):
            name = re.sub(r"^shakedown\b\s*[-:]?\s*", "", activity, flags=re.I).strip()
            name = rally_name_case(name)
            canonical = "Shakedown" + (" - " + name if name else "")
            sessions.append(SourceSession(canonical, current_date, parse_clock(clock), source_timezone, 60))
            continue
        stage_match = re.match(r"^SSS?\s*(\d+)(?:[A-Z])?\b\s*[-:]?\s*(.+)$", activity, re.I)
        if not stage_match:
            stage_match = re.match(r"^(?:WOLF\s+)?POWER\s+STAGE\s*[-:]\s*SSS?\s*(\d+)(?:[A-Z])?\b\s*[-:]?\s*(.+)$", activity, re.I)
        if not stage_match:
            continue
        number, stage_name = stage_match.groups()
        # Some ERC pages prefix a shared route with the number of its second
        # running (for example "SS2 SS5 OBEJO"). The current row number is
        # authoritative; the extra marker is not part of the stage name.
        stage_name = re.sub(r"^(?:SSS?\s*\d+\s+)+", "", stage_name, flags=re.I).strip(" :-")
        if not stage_name:
            continue
        stage_name = rally_name_case(stage_name)
        sessions.append(SourceSession("SS{} - {}".format(number, stage_name), current_date, parse_clock(clock), source_timezone))
    unique = {(item.name, item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, session_number(item.name) or 0))


def json_object(body: str, description: str) -> object:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError("official {} response is not valid JSON".format(description)) from exc


def select_motogp_event(body: str, event: dict) -> dict:
    payload = json_object(body, "MotoGP events")
    if not isinstance(payload, list):
        raise SourceError("official MotoGP events response has an unexpected shape")
    round_number = event.get("roundNumber")
    candidates = [item for item in payload if item.get("kind") == "GP" and item.get("sequence") == round_number]
    if not candidates:
        target = "{} {}".format(event.get("raceName", ""), event.get("circuitName", ""))
        scored = [
            (similarity(target, "{} {} {}".format(item.get("name", ""), item.get("place", ""), (item.get("circuit") or {}).get("name", ""))), item)
            for item in payload if item.get("kind") == "GP"
        ]
        score, selected = max(scored, default=(0.0, None), key=lambda item: item[0])
        candidates = [selected] if selected and score >= 0.2 else []
    if len(candidates) != 1:
        raise SourceError("could not identify one official MotoGP event for this round")
    return candidates[0]


def select_worldsbk_round(body: str, event: dict) -> Tuple[dict, Optional[dict]]:
    payload = json_object(body, "WorldSBK rounds")
    rounds = payload.get("data", []) if isinstance(payload, dict) else []
    round_number = event.get("roundNumber")
    candidates = [item for item in rounds if (item.get("attributes") or {}).get("sequence_order") == round_number]
    if len(candidates) != 1:
        raise SourceError("could not identify one official WorldSBK round")
    selected = candidates[0]
    circuit_id = (((selected.get("relationships") or {}).get("circuit") or {}).get("data") or {}).get("id")
    circuit = next(
        (item for item in payload.get("included", []) if item.get("type") == "circuits" and item.get("id") == circuit_id),
        None,
    )
    return selected, circuit


def api_timezone(value: object) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = ["/".join(part.title() for part in raw.split("/")), raw]
    for candidate in candidates:
        try:
            ZoneInfo(candidate)
            return candidate
        except ZoneInfoNotFoundError:
            continue
    return None


WINDOWS_TO_IANA = {
    "AUS Eastern Standard Time": "Australia/Sydney",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Rome",
}


def motogp_source_timezone(fetch: FetchResult) -> Optional[str]:
    payload = json_object(fetch.body, "MotoGP event")
    return api_timezone(payload.get("time_zone")) if isinstance(payload, dict) else None


def worldsbk_source_timezone(fetch: FetchResult) -> Optional[str]:
    payload = json_object(fetch.body, "WorldSBK sessions")
    circuit = payload.get("circuit") if isinstance(payload, dict) else None
    windows_zone = (circuit or {}).get("attributes", {}).get("time_zone")
    return WINDOWS_TO_IANA.get(windows_zone)


def session_duration(start_value: str, end_value: str) -> Optional[int]:
    try:
        normalized_start = re.sub(r"Z$", "+00:00", re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", start_value))
        normalized_end = re.sub(r"Z$", "+00:00", re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", end_value))
        start = datetime.fromisoformat(normalized_start)
        end = datetime.fromisoformat(normalized_end)
    except (TypeError, ValueError):
        return None
    minutes = int((end - start).total_seconds() // 60)
    return minutes if minutes > 0 else None


def parse_motogp_schedule(fetch: FetchResult, category_name: str, source_timezone: str) -> List[SourceSession]:
    payload = json_object(fetch.body, "MotoGP event")
    broadcasts = payload.get("broadcasts", []) if isinstance(payload, dict) else []
    names = {
        "FP1": "Free Practice 1", "FP2": "Free Practice 2", "FP3": "Free Practice 3",
        "PR": "Practice", "Q1": "Qualifying 1", "Q2": "Qualifying 2",
        "SPR": "Sprint", "WUP": "Warm Up", "RAC": "Race",
    }
    sessions: List[SourceSession] = []
    for item in broadcasts:
        if item.get("type") != "SESSION" or normalize((item.get("category") or {}).get("name")) != normalize(category_name):
            continue
        name = names.get(str(item.get("shortname") or "").upper())
        start = str(item.get("date_start") or "")
        if not name or not re.match(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}", start):
            continue
        sessions.append(SourceSession(
            name, start[:10], start[11:16], source_timezone,
            session_duration(start, str(item.get("date_end") or "")),
        ))
    unique = {(item.name, item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


def parse_worldsbk_schedule(fetch: FetchResult, category_id: str, source_timezone: str) -> List[SourceSession]:
    payload = json_object(fetch.body, "WorldSBK sessions")
    records = payload.get("sessions", []) if isinstance(payload, dict) else []
    sessions: List[SourceSession] = []
    for item in records:
        relationship = (((item.get("relationships") or {}).get("category") or {}).get("data") or {})
        if relationship.get("id") != category_id:
            continue
        attributes = item.get("attributes") or {}
        raw_name = str(attributes.get("description") or attributes.get("brief_description") or "")
        normalized_name = normalize(raw_name)
        practice = re.search(r"free practice\s+(\d+)(?:st|nd|rd|th)?(?:\s+session)?", normalized_name)
        if practice:
            name = "Free Practice {}".format(practice.group(1))
        elif "superpole race" in normalized_name:
            name = "Superpole Race"
        elif "superpole" in normalized_name:
            name = "Superpole"
        elif normalized_name.startswith("warm up"):
            name = "Warmup"
        elif re.fullmatch(r"race\s+[12]", normalized_name):
            name = raw_name.strip()
        else:
            continue
        start = str(attributes.get("start_date_circuit") or "")
        end = str(attributes.get("end_date_circuit") or "")
        if not re.match(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}", start):
            continue
        sessions.append(SourceSession(name, start[:10], start[11:16], source_timezone, session_duration(start, end)))
    unique = {(item.name, item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


def parse_formula1_schedule(fetch: FetchResult) -> List[SourceSession]:
    """Require the public timetable, session status and UTC data to agree.

    F1 emits numeric JSON-LD placeholders even when its timetable says TBC.
    Never interpret those internal timestamps as published session times.
    """
    names = {"practice 1": "Practice 1", "practice 2": "Practice 2",
             "practice 3": "Practice 3", "sprint qualifying": "Sprint Qualifying",
             "sprint": "Sprint Race", "qualifying": "Qualifying", "race": "Race"}
    match = re.search(r'\\?"meetingSessions\\?"\s*:\s*(\[.*?\])\s*,', fetch.body, re.S)
    if not match:
        return []
    try:
        raw = match.group(1)
        records = json.loads(json.loads('"' + raw + '"')) if r'\"' in raw else json.loads(raw)
    except (ValueError, TypeError):
        return []
    published = {}
    for row in re.findall(r"<li\b[^>]*>(.*?)</li>", fetch.body, re.I | re.S):
        clocks = re.findall(r"<time\b[^>]*>(.*?)</time>", row, re.I | re.S)
        if len(clocks) not in (1, 2) or re.search(r"\b(?:TBC|TBD)\b", strip_tags(row), re.I):
            continue
        for span in re.findall(r"<span\b[^>]*>(.*?)</span>", row, re.I | re.S):
            key = normalize(strip_tags(span))
            if key in names:
                published[key] = ([strip_tags(clock).strip() for clock in clocks], strip_tags(row))
    structured = {}
    for script in re.findall(r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", fetch.body, re.I | re.S):
        try:
            payload = json.loads(html.unescape(script).strip())
        except (ValueError, TypeError):
            continue
        for item in payload.get("subEvent", []) if isinstance(payload, dict) else []:
            structured[normalize(str(item.get("name", "")).split(" - ", 1)[0])] = item
    sessions = []
    for item in records:
        key = normalize(str(item.get("description", "")))
        if key not in published or key not in structured:
            continue
        if re.search(r"\b(?:TBC|TBD|provisional|unconfirmed)\b", str(item.get("sessionStatus", "")), re.I):
            continue
        try:
            zone = ZoneInfo(item["timezone"])
            start = datetime.fromisoformat(item["startTime"]).replace(tzinfo=zone)
            end = datetime.fromisoformat(item["endTime"]).replace(tzinfo=zone)
            offset_start = datetime.fromisoformat(item["startTime"] + item["gmtOffset"])
            utc_start = datetime.fromisoformat(structured[key]["startDate"].replace("Z", "+00:00"))
            utc_end = datetime.fromisoformat(structured[key]["endDate"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
            continue
        if utc_start.tzinfo is None or utc_end.tzinfo is None:
            continue
        if start != offset_start or start != utc_start or end != utc_end or end <= start:
            continue
        utc_start, utc_end = utc_start.astimezone(timezone.utc), utc_end.astimezone(timezone.utc)
        clocks, row_text = published[key]
        expected = [utc_start.strftime("%H:%M"), utc_end.strftime("%H:%M")][:len(clocks)]
        if clocks != expected or (len(clocks) == 1 and key != "race"):
            continue
        # The visible day and month must identify this session, too.
        day_month = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})\b", row_text)
        if not day_month or int(day_month.group(1)) != utc_start.day or day_month.group(2).lower() != utc_start.strftime("%b").lower():
            continue
        sessions.append(SourceSession(names[key], utc_start.date().isoformat(),
                                      utc_start.strftime("%H:%M"), "UTC",
                                      int((utc_end - utc_start).total_seconds() // 60)))
    return sorted(sessions, key=lambda item: (item.date, item.local_time, item.name))


def discover_elms_timetable_pdf(event_page: FetchResult) -> Optional[str]:
    candidates = []
    for url, label in extract_links(event_page.body, event_page.final_url):
        if ('/document/download/' in urlparse(url).path or urlparse(url).path.lower().endswith('.pdf')) and re.search(r'\btimetable\b', label, re.I):
            version = re.search(r'\bv(?:ersion)?\s*(\d+)\b', label, re.I)
            candidates.append((int(version.group(1)) if version else 0, url))
    return max(candidates, default=(0, None))[1]


def parse_elms_timetable(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    """Read ELMS's PDF layout rows, excluding shared support categories."""
    title = re.sub(r'^Goodyear\s+', '', event.get('raceName', ''), flags=re.I)
    headers = [normalize(line) for line in fetch.body.splitlines() if 'TIMETABLE' in line.upper()]
    if not headers or any(str(year) not in header or normalize(title) not in header for header in headers):
        raise SourceError('ELMS timetable does not identify the selected event and season')
    current_date = None
    sessions = []
    qualifying = []
    for line in fetch.body.splitlines():
        if re.match(r'^\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b', line, re.I):
            current_date = parse_document_date(line, year)
            continue
        # Some PDF cells are emitted at the end of the line without whitespace
        # ("240\u0027ELMSRACE"). The exact ELMS category followed by an allowed
        # activity still identifies those rows; no neighbouring row is read.
        category = re.search(r'ELMS\s*(FREE PRACTICE [12]|QUALIFYING SESSION\s*-\s*(?:LMGT3|LMP3|LMP2(?:\s+PRO/?AM)?)|RACE)(?=\s{2,}|$)', line, re.I)
        if not category:
            continue
        if re.search(r'\b(?:TBC|TBA|CANCELLED|CANCELED)\b', line, re.I):
            raise SourceError('ELMS timetable contains an unconfirmed or cancelled session')
        clocks = re.match(r'^\s*(\d{1,2}:\d{2})\s+(\d{1,2}:\d{2})(?=\s)', line)
        if not clocks or not current_date:
            raise SourceError('ELMS timetable row has no unambiguous date and start/end time')
        try:
            start, end = (parse_clock(value) for value in clocks.groups())
        except ValueError as exc:
            raise SourceError('ELMS timetable row has an invalid time') from exc
        minutes = lambda clock: int(clock[:2])*60 + int(clock[3:])
        duration = minutes(end) - minutes(start)
        if duration <= 0:
            raise SourceError('ELMS timetable row has an invalid duration')
        label = normalize(category.group(1))
        name = 'Free Practice ' + label[-1] if label.startswith('free practice') else 'Race'
        item = SourceSession(name, current_date, start, source_timezone, duration)
        strict_source_instant(item)
        if label.startswith('qualifying'):
            class_name = label.split('session', 1)[1].strip().replace('pro am', 'proam')
            qualifying.append((class_name, item))
        else:
            sessions.append(item)
    # One existing Qualifying slot represents all four class sessions, gaps
    # included. A missing/duplicated class must not become a partial proposal.
    expected_classes = {'lmgt3', 'lmp3', 'lmp2 proam', 'lmp2'}
    if len(qualifying) != 4 or {name for name, _ in qualifying} != expected_classes:
        raise SourceError('ELMS qualifying timetable is incomplete or ambiguous')
    existing_qualifying = [item for item in event.get('sessions', []) if item.get('kind') == 'qualifying']
    if len(existing_qualifying) != 1 or normalize(existing_qualifying[0].get('name')) != 'qualifying':
        raise SourceError('ELMS calendar qualifying slots require manual review')
    rows = [item for _, item in qualifying]
    if len({item.date for item in rows}) != 1:
        raise SourceError('ELMS qualifying sessions span multiple days')
    rows.sort(key=lambda item: item.local_time)
    for left, right in zip(rows, rows[1:]):
        if strict_source_instant(left) + timedelta(minutes=left.duration_minutes) > strict_source_instant(right):
            raise SourceError('ELMS qualifying sessions overlap')
    first = rows[0]
    end = strict_source_instant(rows[-1]) + timedelta(minutes=rows[-1].duration_minutes)
    sessions.append(SourceSession('Qualifying', first.date, first.local_time, first.timezone, int((end-strict_source_instant(first)).total_seconds()//60)))
    if sorted(item.name for item in sessions) != ['Free Practice 1', 'Free Practice 2', 'Qualifying', 'Race']:
        raise SourceError('ELMS timetable is incomplete or contains duplicate sessions')
    calendar_dates = sorted(item.get('date') for item in event.get('sessions', []) if item.get('date'))
    if not calendar_dates or any(item.date < calendar_dates[0] or item.date > calendar_dates[-1] for item in sessions):
        raise SourceError('ELMS timetable dates differ from the selected calendar event')
    return sorted(sessions, key=lambda item: (item.date, item.local_time))


def parse_official_schedule_document(fetch: FetchResult, year: int, source_timezone: str, categories: Sequence[str]) -> List[SourceSession]:
    sessions: List[SourceSession] = []
    current_date: Optional[str] = None
    category_terms = [normalize(value) for value in categories]
    session_pattern = re.compile(
        r"\b(hyperpole|pole\s+shootout|pre[-\s]+qualifying|free\s+practice(?:\s*#?\d+)?|practice(?:\s*#?\d+)?|"
        r"qualif(?:y|ying)(?:\s+(?:session\s+|driver\s+)?#?\d+)?(?:\s*-\s*(?:gt3|gt4))?|"
        r"qualifications?|warm\s*up|race(?:\s*#?\d+)?)\b",
        re.I,
    )
    excluded = ("press conference", "briefing", "inspection", "track walk", "pit walk", "grid walk", "formation lap", "recon lap", "course clearance", "autograph", "forms due", "tire sheets due", "finish:")
    dated_lines: List[Tuple[str, Optional[str]]] = []
    for page in fetch.body.split("\f"):
        page_lines = document_lines(page)
        dated = [(index, parse_document_date(line, year)) for index, line in enumerate(page_lines)]
        dated = [(index, value) for index, value in dated if value]
        column_dates = [
            (index, value) for index, value in dated
            if re.match(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", page_lines[index], re.I)
        ]
        first_clock = next((index for index, line in enumerate(page_lines) if re.search(r"\b\d{1,2}:\d{2}", line)), None)
        # SRO's multi-column PDFs are extracted row-first and put the column
        # date headings at the end of each page. Their repeated Broadcast
        # header reliably marks the start of each day's column. A page may
        # begin with the continuation of the previous day.
        trailing_column_dates = bool(column_dates and first_clock is not None and min(index for index, _ in column_dates) > first_clock)
        if trailing_column_dates:
            page_dates = [value for _, value in column_dates]
            header_indexes = [
                index for index, line in enumerate(page_lines)
                if normalize(line).startswith("broadcast start end duration category session")
            ]
            first_header = header_indexes[0] if header_indexes else len(page_lines)
            if first_header > 0:
                dated_lines.extend((line, current_date) for line in page_lines[:first_header] if not parse_document_date(line, year))
            for group_index, start_index in enumerate(header_indexes):
                end_index = header_indexes[group_index + 1] if group_index + 1 < len(header_indexes) else len(page_lines)
                group_date = page_dates[min(group_index, len(page_dates) - 1)]
                dated_lines.extend(
                    (line, group_date)
                    for line in page_lines[start_index:end_index]
                    if not parse_document_date(line, year)
                )
            current_date = page_dates[-1]
            continue
        for line in page_lines:
            parsed_date = parse_document_date(line, year)
            if parsed_date:
                current_date = parsed_date
            dated_lines.append((line, current_date))

    lines = [line for line, _ in dated_lines]
    for index, (line, line_date) in enumerate(dated_lines):
        current_date = line_date
        if not current_date:
            continue
        # Official programme pages commonly render one session as three
        # adjacent text nodes: time range, championship, session name. Start
        # at the time node and only look forward; looking at a sliding window
        # from every line can accidentally pair a time with the next session.
        if not re.search(r"\b\d{1,2}:\d{2}", line):
            continue
        preceding_row: List[str] = []
        for preceding in reversed(lines[max(0, index - 4):index]):
            if re.search(r"\b\d{1,2}:\d{2}", preceding) or parse_document_date(preceding, year):
                break
            preceding_row.insert(0, preceding)
        # IndyCar PDFs place the championship on the line before a timed
        # activity, and put race titles plus lap counts before the start time.
        # Only bring that prefix in for those shapes so ordinary timetable
        # rows cannot inherit the previous session's label.
        # A timed Indy activity already has its category immediately before
        # it. Stop at the time line: looking forward would attach the category
        # of the next championship and make one row match both series. Race
        # titles with lap counts use the same prefix-only layout.
        if session_pattern.search(line) or any("laps" in normalize(value) for value in preceding_row):
            row = preceding_row + [line]
        else:
            row = [line]
            for following in lines[index + 1:index + 4]:
                # DTM-style programme pages put championship and session name
                # after the clock. A new clock/date starts the next row.
                if re.search(r"\b\d{1,2}:\d{2}", following) or parse_document_date(following, year):
                    break
                row.append(following)
        segment = " ".join(row)
        normalized_segment = normalize(segment)
        if not any(term in normalized_segment for term in category_terms):
            continue
        if any(term in normalized_segment for term in excluded):
            continue
        name_match = session_pattern.search(segment)
        clocks = list(re.finditer(r"\b\d{1,2}:\d{2}\s*(?:[AP]M)?\b", segment, re.I))
        if not clocks:
            continue
        name = " ".join(name_match.group(1).split()) if name_match else ("Race" if "laps" in normalized_segment else "")
        kind = normalize_kind(name)
        if not kind:
            continue
        try:
            start = parse_clock(clocks[0].group())
        except ValueError:
            continue
        duration = None
        if len(clocks) > 1:
            try:
                start_value = datetime.strptime(start, "%H:%M")
                end_value = datetime.strptime(parse_clock(clocks[1].group()), "%H:%M")
                minutes = int((end_value - start_value).total_seconds() // 60)
                duration = minutes if minutes > 0 else minutes + 24 * 60
            except ValueError:
                pass
        sessions.append(SourceSession(name, current_date, start, source_timezone, duration))
    unique = {}
    for session in sessions:
        unique[(normalize(session.name), session.date, session.local_time)] = session
    return list(unique.values())


def parse_imsa_event_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    """Read WeatherTech-only sessions from an IMSA event's Event Schedule."""
    lines = document_lines(fetch.body)
    start = next((index + 1 for index, line in enumerate(lines) if normalize(line) == "event schedule"), 0)
    if not start:
        return []
    end = next(
        (index for index in range(start, len(lines)) if normalize(lines[index]) in {"official partners", "more stories"}),
        len(lines),
    )
    lines = lines[start:end]
    sessions: List[SourceSession] = []
    current_date: Optional[str] = None
    race_name = normalize(event.get("raceName"))
    for index, line in enumerate(lines):
        parsed_date = parse_document_date(line, year)
        if parsed_date:
            current_date = parsed_date
            continue
        if not current_date or not re.search(r"\b\d{1,2}:\d{2}\s*[AP]M\b", line, re.I):
            continue
        row = [line]
        for following in lines[index + 1:index + 3]:
            if parse_document_date(following, year) or re.search(r"\b\d{1,2}:\d{2}\s*[AP]M\b", following, re.I):
                break
            row.append(following)
        segment = " ".join(row)
        clocks = list(re.finditer(r"\b\d{1,2}:\d{2}\s*[AP]M\b", segment, re.I))
        if not clocks:
            continue
        label = re.sub(r"\b\d{1,2}:\d{2}\s*[AP]M\b|\bto\b|\bET\b", " ", segment, flags=re.I)
        label = " ".join(label.split())
        normalized_label = normalize(label)
        is_weathertech = "weathertech championship" in normalized_label
        is_main_race = bool(race_name and (race_name in normalized_label or similarity(race_name, normalized_label) >= 0.5))
        if is_weathertech and "practice" in normalized_label:
            number = re.search(r"\bpractice\s*(\d+)?", label, re.I)
            name = "Practice{}".format(" " + number.group(1) if number and number.group(1) else "")
        elif is_weathertech and "qualif" in normalized_label:
            name = "Qualifying"
        elif is_main_race:
            name = "Race"
        else:
            continue
        try:
            local_time = parse_clock(clocks[0].group())
        except ValueError:
            continue
        duration = None
        if len(clocks) > 1:
            start_clock = datetime.strptime(local_time, "%H:%M")
            end_clock = datetime.strptime(parse_clock(clocks[1].group()), "%H:%M")
            minutes = int((end_clock - start_clock).total_seconds() // 60)
            duration = minutes if minutes > 0 else minutes + 24 * 60
        sessions.append(SourceSession(name, current_date, local_time, source_timezone, duration))
    unique = {(normalize(item.name), item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


def parse_ical_datetime(key: str, value: str, fallback_timezone: str) -> Optional[datetime]:
    try:
        parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        try:
            parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M")
        except ValueError:
            return None
    if value.endswith("Z"):
        return parsed.replace(tzinfo=timezone.utc)
    tzid = re.search(r"(?:^|;)TZID=([^;:]+)", key, re.I)
    zone_name = tzid.group(1) if tzid else fallback_timezone
    try:
        return parsed.replace(tzinfo=ZoneInfo(zone_name))
    except ZoneInfoNotFoundError:
        return None


def parse_ical_sessions(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    unfolded: List[str] = []
    for raw_line in fetch.body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)
    sessions: List[SourceSession] = []
    current: Dict[str, Tuple[str, str]] = {}
    inside = False
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current, inside = {}, True
            continue
        if line == "END:VEVENT" and inside:
            summary_entry = current.get("SUMMARY")
            start_entry = current.get("DTSTART")
            end_entry = current.get("DTEND")
            if summary_entry and start_entry:
                summary = summary_entry[1]
                for encoded, decoded in ((r"\n", " "), (r"\,", ","), (r"\;", ";"), (r"\\", "\\")):
                    summary = summary.replace(encoded, decoded)
                name = summary.split(" - ", 1)[-1].strip()
                start = parse_ical_datetime(*start_entry, source_timezone)
                end = parse_ical_datetime(*end_entry, source_timezone) if end_entry else None
                if start and normalize_kind(name):
                    local_start = start.astimezone(ZoneInfo(source_timezone))
                    if local_start.year == year:
                        duration = int((end - start).total_seconds() // 60) if end else None
                        sessions.append(SourceSession(
                            name, local_start.date().isoformat(), local_start.strftime("%H:%M"),
                            source_timezone, duration if duration and duration > 0 else None,
                            start.astimezone(timezone.utc).strftime("%H:%M"),
                        ))
            current, inside = {}, False
            continue
        if not inside or ":" not in line:
            continue
        key, value = line.split(":", 1)
        base_key = key.split(";", 1)[0].upper()
        if base_key in {"SUMMARY", "DTSTART", "DTEND"}:
            current[base_key] = (key, value)
    return sessions


def parse_fia_track_schedule(fetch: FetchResult, source_timezone: str) -> List[SourceSession]:
    """Read F2/F3 sessions from the official site's embedded Next.js data.

    The current FIA Formula 2 and Formula 3 sites no longer render the schedule
    as an HTML table. Their server response contains a JSON ``meetingSessions``
    array inside a React flight string instead. Keep the public event page as
    the evidence URL and decode only that narrowly scoped official array.
    """
    match = re.search(
        r'\\?"meetingSessions\\?"\s*:\s*(\[.*?\])\s*,\s*\\?"season\\?"',
        fetch.body,
        re.S,
    )
    if not match:
        return []

    raw = match.group(1)
    try:
        # In a Next.js flight script the JSON is itself stored in a JSON
        # string, so its quotes are escaped once. Ordinary JSON is accepted
        # too, which keeps fixture tests and future server changes simple.
        decoded = json.loads('"{}"'.format(raw)) if r'\"' in raw else raw
        records = json.loads(decoded)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []

    sessions: List[SourceSession] = []
    for item in records if isinstance(records, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("shortName") or item.get("session") or "").strip()
        if normalize_kind(name) not in {"practice", "qualifying", "sprintRace", "featureRace", "race"}:
            continue
        start = str(item.get("startTime") or "")
        end = str(item.get("endTime") or "")
        if not re.match(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}", start):
            continue
        timezone_name = api_timezone(item.get("timezone")) or source_timezone

        duration = session_duration(start, end)
        # The source occasionally carries the following day in endTime while
        # the displayed clock range remains correct. Recover that range
        # without accepting an impossible day-long circuit session.
        if duration is not None and duration > 360 and re.match(r"^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}", end):
            start_minutes = int(start[11:13]) * 60 + int(start[14:16])
            end_minutes = int(end[11:13]) * 60 + int(end[14:16])
            clock_duration = end_minutes - start_minutes
            duration = clock_duration if 0 < clock_duration <= 360 else None

        utc_hint = None
        offset = str(item.get("gmtOffset") or "")
        if re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
            try:
                utc_hint = datetime.fromisoformat(start + offset).astimezone(timezone.utc).strftime("%H:%M")
            except ValueError:
                pass
        sessions.append(SourceSession(name, start[:10], start[11:16], timezone_name, duration, utc_hint))

    unique = {(normalize(item.name), item.date, item.local_time): item for item in sessions}
    return sorted(unique.values(), key=lambda item: (item.date, item.local_time, item.name))


class Series24HScheduleParser(HTMLParser):
    """Read only the published race timetable, never countdown/result data."""
    def __init__(self):
        super().__init__()
        self.in_schedule = False
        self.row = None
        self.depth = 0
        self.capture_name = False
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get('class', '').split())
        if tag == 'section' and 'headerRaceBlockTime' in classes:
            self.in_schedule = True
        if not self.in_schedule:
            return
        if tag == 'div' and 'entry' in classes:
            self.row = {'date': attrs.get('data-date', ''), 'name': ''}
            self.depth = 1
        elif self.row is not None and tag == 'div':
            self.depth += 1
        if self.row is not None and tag == 'span':
            if 'name' in classes:
                self.capture_name = True
            if 'startTime' in classes:
                self.row.update(start=attrs.get('data-time', ''), zone=attrs.get('data-tz', ''))
            if 'endTime' in classes:
                self.row['end'] = attrs.get('data-time', '')

    def handle_data(self, data):
        if self.row is not None and self.capture_name:
            self.row['name'] += data

    def handle_endtag(self, tag):
        if tag == 'span':
            self.capture_name = False
        if self.row is not None and tag == 'div':
            self.depth -= 1
            if self.depth == 0:
                self.rows.append(self.row)
                self.row = None
        if tag == 'section':
            self.in_schedule = False


def parse_24hseries_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    expected_title = normalize('{} {}'.format(event.get('raceName', ''), year))
    if normalize(fetch.title) != expected_title:
        raise SourceError('24H Series page does not identify the selected event and season')
    parser = Series24HScheduleParser()
    parser.feed(fetch.body)
    sessions = []
    qualifying = []
    qualifying_unconfirmed = False
    for row in parser.rows:
        name = ' '.join(row['name'].split())
        label = normalize(name)
        is_qualifying = bool(re.match(r'^qualifying(?:\s+\d+)?(?:\s|$)', label))
        if re.search(r'\b(tbc|tba|cancelled|canceled|provisional)\b', label):
            qualifying_unconfirmed |= is_qualifying
            continue
        if re.fullmatch(r'free practice(?: \d+)?', label):
            canonical = name
        elif label.startswith('night practice'):
            canonical = 'Night Practice'
        elif is_qualifying:
            canonical = name
        elif re.match(r'^(?:start|restart) (?:of )?(?:the )?(?:race|michelin (?:12h|24h))\b', label):
            part = re.search(r'\bpart (\d+)\b', label)
            canonical = 'Race Part ' + part.group(1) if part else 'Race'
        else:
            continue
        if row.get('zone') != source_timezone:
            raise SourceError('24H Series timetable timezone differs from the registered circuit timezone')
        try:
            start = datetime.strptime(row['date'], '%B %d, %Y %H:%M')
            clock = parse_clock(row.get('start', ''))
        except ValueError as exc:
            raise SourceError('24H Series session has no confirmed valid start time') from exc
        if start.year != year or start.strftime('%H:%M') != clock:
            raise SourceError('24H Series timetable date/time attributes disagree')
        duration = None
        if re.fullmatch(r'\d{1,2}:\d{2}', row.get('end', '')):
            finish = parse_clock(row['end'])
            minutes = lambda t: int(t[:2]) * 60 + int(t[3:])
            duration = (minutes(finish) - minutes(clock)) % 1440
            if duration == 0:
                raise SourceError('24H Series session has an ambiguous end time')
        if canonical == 'Race' and duration is None:
            duration = 1440 if '24h' in normalize(event.get('raceName')) else 720
        item = SourceSession(canonical, start.date().isoformat(), clock, source_timezone, duration)
        strict_source_instant(item)
        (qualifying if is_qualifying else sessions).append(item)
    # Existing 2026 calendars generally represent all class/driver qualifying
    # runs as one block. Keep that format instead of creating nine duplicates.
    if qualifying and not qualifying_unconfirmed:
        existing = [x for x in event.get('sessions', []) if x.get('kind') == 'qualifying']
        if len(existing) == 1 and normalize(existing[0].get('name')) == 'qualifying':
            days = {x.date for x in qualifying}
            if len(days) != 1 or any(x.duration_minutes is None for x in qualifying):
                raise SourceError('24H Series qualifying block is incomplete or spans multiple days')
            first = min(qualifying, key=lambda x: x.local_time)
            end = max(strict_source_instant(x) + timedelta(minutes=x.duration_minutes) for x in qualifying)
            duration = int((end - strict_source_instant(first)).total_seconds() // 60)
            sessions.append(SourceSession('Qualifying', first.date, first.local_time, first.timezone, duration))
        elif any(not session_family(x.get('name', '')) for x in existing):
            raise SourceError('24H Series qualifying classes cannot be mapped unambiguously to these numbered calendar slots')
        else:
            sessions.extend(qualifying)
    return sorted(set(sessions), key=lambda x: (x.date, x.local_time, x.name))


def parse_dtm_api(fetch: FetchResult, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse the public official DTM event API.

    DTM stores timetable instants as ISO-8601 values with an explicit UTC
    offset. Convert those instants to track time before handing them to the
    normal timezone pipeline; treating the clock component as local would
    shift European events by one or two hours.
    """
    try:
        payload = json.loads(fetch.body)
    except json.JSONDecodeError as exc:
        raise SourceError("officiële DTM-API gaf geen geldige JSON terug") from exc
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list) or not events:
        return []
    try:
        track_zone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise SourceError("unknown IANA timezone {}".format(source_timezone)) from exc
    sessions: List[SourceSession] = []
    for item in events[0].get("timetable", []):
        if item.get("raceSeries") != "DTM":
            continue
        name = " ".join(str(item.get("label") or "").split())
        if not normalize_kind(name):
            continue
        try:
            start = datetime.fromisoformat(str(item.get("start") or "").replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(item.get("end") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None or end.tzinfo is None:
            continue
        local_start = start.astimezone(track_zone)
        if local_start.year != year:
            continue
        duration = int((end - start).total_seconds() // 60)
        sessions.append(SourceSession(
            name,
            local_start.date().isoformat(),
            local_start.strftime("%H:%M"),
            source_timezone,
            duration if duration > 0 else None,
            start.astimezone(timezone.utc).strftime("%H:%M"),
        ))
    return sessions


def parse_formula_e_schedule(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    """Parse official Formula E preview rows with track-local and UTC evidence."""
    try:
        track_zone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise SourceError("unknown IANA timezone {}".format(source_timezone)) from exc
    event_dates = {str(item.get("date")) for item in event.get("sessions", []) if item.get("date")}
    sessions: List[SourceSession] = []
    row_pattern = re.compile(
        r"^\s*((?:free\s+)?practice(?:\s+\d+)?|qualifying|qualifications?|race(?:\s+\d+)?)\s*:",
        re.I,
    )
    for line in document_lines(fetch.body):
        if normalize(line).startswith("rookie free practice"):
            continue
        name_match = row_pattern.search(line)
        local_match = re.search(r"\b(\d{1,2}:\d{2})\s*local\b", line, re.I)
        session_date = parse_document_date(line, year)
        if not name_match or not local_match or not session_date:
            continue
        if event_dates and session_date not in event_dates:
            continue
        name = " ".join(name_match.group(1).split())
        if not normalize_kind(name):
            continue
        try:
            local_clock = parse_clock(local_match.group(1))
        except ValueError:
            continue
        utc_match = re.search(r"\b(\d{1,2}:\d{2})\s*UTC\b", line, re.I)
        utc_hint = parse_clock(utc_match.group(1)) if utc_match else None
        if utc_hint:
            local = datetime.fromisoformat("{}T{}:00".format(session_date, local_clock)).replace(tzinfo=track_zone)
            if local.astimezone(timezone.utc).strftime("%H:%M") != utc_hint:
                continue
        sessions.append(SourceSession(name, session_date, local_clock, source_timezone, None, utc_hint))

    # Older RaceDay Formula E calendars intentionally contain one generic
    # practice. If the official event/date selection also leaves one practice,
    # use that existing label so debug matching is deterministic.
    existing_practice = [item for item in event.get("sessions", []) if item.get("kind") == "practice"]
    parsed_practice = [item for item in sessions if normalize_kind(item.name) == "practice"]
    if len(existing_practice) == len(parsed_practice) == 1 and session_number(existing_practice[0].get("name", "")) is None:
        official = parsed_practice[0]
        sessions = [
            SourceSession(existing_practice[0].get("name") or "Practice", item.date, item.local_time, item.timezone, item.duration_minutes, item.utc_hint)
            if item is official else item
            for item in sessions
        ]
    unique = {(normalize(item.name), item.date, item.local_time): item for item in sessions}
    return list(unique.values())


def strict_source_instant(session: SourceSession) -> datetime:
    try:
        zone = ZoneInfo(session.timezone)
    except ZoneInfoNotFoundError as exc:
        raise SourceError("unknown IANA timezone {}".format(session.timezone)) from exc
    try:
        naive = datetime.fromisoformat("{}T{}:00".format(session.date, session.local_time))
    except (ValueError, TypeError) as exc:
        raise SourceError("invalid official session date or time") from exc
    candidates = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            candidates.append(aware.astimezone(timezone.utc))
    unique = {candidate.isoformat(): candidate for candidate in candidates}
    if not unique:
        raise SourceError("non-existent local time {} {} {}".format(session.date, session.local_time, session.timezone))
    if len(unique) > 1:
        if session.utc_hint:
            for candidate in unique.values():
                if candidate.strftime("%H:%M") == session.utc_hint:
                    return candidate
        raise SourceError("ambiguous local time {} {} {}".format(session.date, session.local_time, session.timezone))
    result = next(iter(unique.values()))
    if session.utc_hint and result.strftime("%H:%M") != session.utc_hint:
        raise SourceError("official UTC time disagrees with the source timezone")
    return result


def editor_value(session: SourceSession, editor_timezone: str) -> Tuple[str, str, str]:
    instant = strict_source_instant(session)
    try:
        editor = instant.astimezone(ZoneInfo(editor_timezone))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SourceError("unknown editor timezone {}".format(editor_timezone)) from exc
    return editor.strftime("%Y-%m-%d"), editor.strftime("%H:%M"), instant.isoformat().replace("+00:00", "Z")


def session_number(name: str) -> Optional[int]:
    # A class (GT3), race duration (6 Hour Race), or stage-name suffix is
    # not a session number. Only read a number attached to a session label.
    match = re.search(r"(?:^|\b)(?:sss?|special stage|practice|qualifying|qualification|race(?: part)?|shootout|ttso|hyperpole|session|duel)\s*(\d+)\b", normalize(name))
    return int(match.group(1)) if match else None


def session_alias(name: str) -> str:
    value = normalize(name)
    value = re.sub(r"^free practice\b", "practice", value)
    value = re.sub(r"\bwarm up\b", "warmup", value)
    value = re.sub(r"\b(?:top 10 shootout|ttso)\b", "top ten shootout", value)
    return re.sub(r"^(race|qualifying|top ten shootout) 1$", r"\1", value)


def session_family(name: str) -> Tuple[str, ...]:
    """Discriminators that must never be discarded by kind/number fallback."""
    value = session_alias(name)
    markers = [word for word in ('warmup', 'shootout', 'superpole', 'hyperpole',
               'night', 'bronze', 'pre qualifying', 'final', 'high line',
               'cancelled', 'canceled', 'resumption', 'rescheduled')
               if re.search(r"\b" + word + r"\b", value)]
    markers += re.findall(r"\b(?:gt[234]|lmgt3|lmp[23]|hypercar|pro am|group [a-z]|top \d+|fast \d+)\b", value)
    return tuple(sorted(markers))


def match_session(source: SourceSession, sessions: Sequence[dict]) -> Tuple[Optional[dict], Optional[str]]:
    source_kind = normalize_kind(source.name)
    if source_kind not in SUPPORTED_KINDS:
        return None, "unknown official session type"

    def select(candidates):
        if len(candidates) > 1:
            dated = [item for item in candidates if item.get("date") == source.date]
            if len(dated) == 1:
                return dated[0], None
            return None, "multiple equivalent sessions make automatic matching ambiguous"
        return (candidates[0], None) if candidates else (None, None)

    exact = [item for item in sessions if normalize(item.get("name")) == normalize(source.name)]
    if exact:
        return select(exact)
    aliases = [item for item in sessions if session_alias(item.get("name", "")) == session_alias(source.name)]
    if aliases:
        return select(aliases)
    cancelled = [item for item in sessions if re.search(r"\b(cancelled|canceled)\b", normalize(item.get("name")))
                 and session_alias(re.sub(r"\s*\((?:cancelled|canceled)\)\s*$", "", item.get("name", ""), flags=re.I)) == session_alias(source.name)]
    if cancelled:
        return None, "The matching calendar session is cancelled; review its status manually."
    source_number = session_number(source.name)
    same_kind = [item for item in sessions
                 if (normalize_kind(item.get("name", "")) or item.get("kind")) == source_kind
                 and session_family(item.get("name", "")) == session_family(source.name)]
    numbered = [item for item in same_kind if session_number(item.get("name", "")) == source_number]
    if source_number is not None and numbered:
        return select(numbered)
    if source_number is None:
        same_date = [item for item in same_kind if item.get("date") == source.date]
        if len(same_date) == 1:
            return same_date[0], None
        if len(same_kind) == 1:
            return same_kind[0], None
    elif source_number == 1:
        generic = [item for item in same_kind if session_number(item.get("name", "")) is None]
        if generic:
            return select(generic)
    if same_kind:
        # Explicit distinct numbered slots are new sessions, not conflicts.
        if source_number is not None and all(session_number(item.get("name", "")) is not None for item in same_kind):
            return None, None
        return None, "multiple or incompatible {} sessions make automatic matching ambiguous".format(source_kind)
    return None, None


def fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def identity_key(proposal: dict) -> str:
    session_key = proposal.get("sessionId") or (proposal.get("proposed") or {}).get("sessionId") or proposal.get("sessionName")
    if proposal.get("proposalType") == "conflict" and proposal.get("sourceTime"):
        session_key = "{}:{}:{}".format(session_key or "", proposal["sourceTime"].get("date"), proposal["sourceTime"].get("time"))
    return "|".join(str(value or "") for value in (proposal.get("seriesId"), proposal.get("calendarFile"), proposal.get("eventId"), session_key))


def proposal_base(series_cfg: dict, filename: str, event: dict, source: FetchResult, checked_at: str) -> dict:
    return {
        "seriesId": series_cfg["seriesId"],
        "seriesName": series_cfg["name"],
        "calendarFile": filename,
        "eventId": event.get("id"),
        "eventName": event.get("raceName") or event.get("circuitName"),
        "roundNumber": event.get("roundNumber"),
        "source": {
            "stableUrl": source.stable_url,
            "finalUrl": source.final_url,
            "title": source.title,
            "checkedAt": checked_at,
            "lastModified": source.last_modified,
            **({"confirmationPolicy": "bsb-official-pdf-v1"}
               if series_cfg.get("sourceKind") == "bsb-timetable" and source.title == "Official timetable PDF" else {}),
            **({"confirmationPolicy": "f1-published-schedule-v1"}
               if series_cfg.get("sourceKind") == "formula1-jsonld" else {}),
        },
    }


def unresolved_proposals(series_cfg: dict, filename: str, event: dict, source: FetchResult, checked_at: str, reason: str) -> List[dict]:
    proposals = []
    for session in event.get("sessions", []):
        # A dated TBC row already communicates everything the scanner knows.
        # Do not show a meaningless date+TBC -> TBC proposal in the editor.
        if session.get("timeLocal") is not None or session.get("date"):
            continue
        proposal = proposal_base(series_cfg, filename, event, source, checked_at)
        proposal.update({
            "proposalType": "unresolved",
            "sessionId": session.get("id"),
            "sessionName": session.get("name"),
            "current": {"date": session.get("date"), "timeLocal": None, "durationMinutes": session.get("durationMinutes")},
            "proposed": None,
            "editorTimeZone": None,
            "status": "unresolved",
            "reason": reason,
        })
        proposal["fingerprint"] = fingerprint({
            "seriesId": proposal["seriesId"], "calendarFile": filename,
            "eventId": proposal["eventId"], "sessionId": proposal["sessionId"],
            "source": source.final_url, "proposed": None,
        })
        proposal["id"] = "schedule-" + proposal["fingerprint"][:16]
        proposals.append(proposal)
    return proposals


def build_event_proposals(
    series_cfg: dict,
    filename: str,
    event: dict,
    source: FetchResult,
    official_sessions: Sequence[SourceSession],
    editor_timezone: str,
    checked_at: str,
    include_filled: bool = False,
    replace_filled: bool = False,
) -> List[dict]:
    proposals: List[dict] = []
    existing = event.get("sessions", [])
    if series_cfg.get("seriesId") == "fd" and filename == "fd_2026.json":
        existing = [dict(item, timeLocal=item.get("timeLocal") or item.get("timeUTC")) for item in existing]
    official_sessions = list(dict.fromkeys(official_sessions))
    matched_ids = set()
    supercars = series_cfg.get("sourceKind") == "supercars-embedded-schedule"
    race_numbers = sorted({session_number(item.name) for item in official_sessions if normalize_kind(item.name) == "race" and session_number(item.name) is not None}) if supercars else []
    runs = []
    for number in race_numbers:
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    longest = max(runs, key=len) if runs else []
    # A discontinuous race number in the publisher's data needs review; do not
    # silently count it as an extra championship race.
    suspect_numbers = set(race_numbers) - set(longest) if len(longest) >= 2 and len(runs) > 1 else set()
    weekend_races = sorted([item for item in official_sessions if normalize_kind(item.name) == "race" and session_number(item.name) not in suspect_numbers], key=lambda item: (item.date, item.local_time)) if supercars else []

    supercars_names = {}
    if supercars:
        for label, group in [
            ("Race", weekend_races),
            ("Practice", [item for item in official_sessions if normalize_kind(item.name) == "practice" and "warmup" not in session_family(item.name)]),
            ("Qualifying", [item for item in official_sessions if normalize_kind(item.name) == "qualifying" and not re.search(r"shootout|\bttso\b", item.name, re.I)]),
            ("Top Ten Shootout", [item for item in official_sessions if normalize_kind(item.name) == "qualifying" and re.search(r"shootout|\bttso\b", item.name, re.I)]),
        ]:
            for number, item in enumerate(sorted(group, key=lambda item: (item.date, item.local_time)), 1):
                supercars_names[id(item)] = "{} {}".format(label, number)

    # Repeated source rows must never create a second session. Contradictory
    # copies stay reviewable instead of choosing an arbitrary published time.
    for official in sorted(official_sessions, key=lambda item: (item.date, item.local_time, normalize(item.name))):
        kind = specific_kind(series_cfg.get("sourceKind"), official.name, normalize_kind(official.name))
        if not kind:
            continue
        available = [item for item in existing if item.get("id") not in matched_ids]
        try:
            proposed_date, proposed_time, utc_instant = editor_value(official, editor_timezone)
            conversion_conflict = None
        except SourceError as exc:
            conversion_conflict = str(exc)
            proposed_date = proposed_time = utc_instant = None
        matching_official = SourceSession(supercars_names.get(id(official), official.name), proposed_date or official.date, proposed_time or official.local_time, editor_timezone, official.duration_minutes)
        matched, conflict = (match_academy_session(official, official_sessions, available, existing)
                             if series_cfg.get("sourceKind") == "f1academy-schedule"
                             else match_session(matching_official, available))
        if series_cfg.get("sourceKind") == "fd-timetable":
            matched, conflict = match_fd(official, official_sessions, existing, available)
        if supercars and kind == "race":
            # Match on race day, never on the season/weekend number alone.
            same_day = [item for item in available if normalize_kind(item.get("name", "")) == "race" and item.get("date") == (proposed_date or official.date)]
            if session_number(official.name) in suspect_numbers:
                matched, conflict = None, "Het officiële racenummer valt buiten de reeks van dit weekend. Controleer deze extra race op de bronpagina."
            elif len(same_day) == 1:
                matched, conflict = same_day[0], None
            elif len(same_day) > 1:
                matched, conflict = None, "Meerdere races op dezelfde dag: controleer de koppeling handmatig."
            else:
                matched, conflict = None, None
        filled_session = bool(matched and matched.get("timeLocal") is not None)
        if filled_session and not (include_filled or replace_filled):
            matched_ids.add(matched.get("id"))
            continue
        contradictory = [item for item in official_sessions if normalize(item.name) == normalize(official.name) and item.date == official.date]
        if len(contradictory) > 1:
            conflict = "Conflicting official rows for the same session; review the source timetable."
        conflict = conversion_conflict or conflict
        is_match = bool(filled_session and matched.get("date") == proposed_date and matched.get("timeLocal") == proposed_time)
        proposed_name = (
            rally_session_name_case((matched or {}).get("name") or official.name)
            if kind == "stage" else official.name
        )
        if supercars:
            proposed_name = supercars_names.get(id(official), official.name)
        if matched and series_cfg.get("sourceKind") in {"fd-timetable", "bsb-timetable"}:
            proposed_name = matched["name"]
            kind = matched.get("kind") or kind
        if matched and session_alias(matched.get("name", "")) == session_alias(proposed_name):
            proposed_name = matched["name"]
        # This is a time scanner. A safely matched session with unchanged
        # timing must not become a time correction solely to rename its label.
        if is_match:
            proposed_name = matched.get("name") or proposed_name
        if series_cfg.get("sourceKind") in {"superformula-timetable", "supergt-timetable", "f1academy-schedule", "elms-timetable", "fd-timetable"} and official.duration_minutes is not None:
            is_match = bool(is_match and matched and matched.get("durationMinutes") == official.duration_minutes)
        if matched and re.search(r"\b(cancelled|canceled)\b", normalize(matched.get("name"))):
            conflict = "The calendar session is cancelled; review its status before changing it."
        proposal = proposal_base(series_cfg, filename, event, source, checked_at)
        actionable_correction = bool(filled_session and replace_filled and not is_match and not conflict)
        proposal.update({
            "proposalType": "conflict" if conflict else "time-update" if actionable_correction else ("verification" if filled_session else ("conflict" if conflict else ("time-update" if matched else "new-session"))),
            "sessionId": matched.get("id") if matched else None,
            "sessionName": proposed_name if supercars and not conflict else (matched.get("name") if matched else official.name),
            "current": {
                "name": matched.get("name") if matched else None,
                "kind": matched.get("kind") if matched else None,
                "date": matched.get("date") if matched else None,
                "timeLocal": matched.get("timeLocal") if matched else None,
                "durationMinutes": matched.get("durationMinutes") if matched else None,
            },
            "sourceSessionName": official.name,
            "sourceTime": {"date": official.date, "time": official.local_time, "timeZone": official.timezone},
            "editorTimeZone": editor_timezone,
            "proposed": None if conflict else {
                "date": proposed_date,
                "timeLocal": proposed_time,
                "durationMinutes": official.duration_minutes or (matched or {}).get("durationMinutes") or (15 if kind == "stage" else 60),
                "name": proposed_name,
                "kind": kind,
                "utcInstant": utc_instant,
                "sessionId": (matched or {}).get("id") or "{}-official-{}".format(event.get("id"), fingerprint({"name": official.name, "date": official.date, "time": official.local_time})[:12]),
            },
            "dateChanged": bool(matched and proposed_date and matched.get("date") != proposed_date),
            "status": "requires_review" if conflict else ("verified" if is_match else ("open" if actionable_correction else "mismatch")) if filled_session else ("requires_review" if conflict else "open"),
            "reason": conflict or (
                "Debugcontrole: de ingevulde sessie komt overeen met de officiële bron."
                if is_match else ("De ingevulde sessie wordt vervangen door de nieuwste officiële tijd." if actionable_correction else "Debugcontrole: de ingevulde sessie wijkt af van de officiële bron.")
            ) if filled_session else (conflict or "Official session time is available."),
            "debug": bool(filled_session and not replace_filled),
        })
        fp_data = {
            "seriesId": proposal["seriesId"], "eventId": proposal["eventId"],
            "sessionId": proposal["sessionId"], "source": source.final_url,
            "sourceTime": proposal["sourceTime"], "proposed": proposal["proposed"],
        }
        # Keep normal and replace-filled scans on the same stable identity. Debug
        # comparisons are intentionally separate and must not affect live proposals.
        if proposal["source"].get("confirmationPolicy"):
            fp_data["confirmationPolicy"] = proposal["source"]["confirmationPolicy"]
        if proposal["debug"]:
            fp_data["mode"] = "debug"
        proposal["fingerprint"] = fingerprint(fp_data)
        proposal["id"] = "schedule-" + proposal["fingerprint"][:16]
        proposals.append(proposal)
        if matched:
            matched_ids.add(matched.get("id"))

    # An undated TBC session must either get a proposal or an explicit unresolved
    # record. A row whose date is already known needs no TBC -> TBC proposal.
    covered = {p.get("sessionId") for p in proposals if p.get("sessionId")}
    for session in existing:
        if session.get("timeLocal") is None and not session.get("date") and session.get("id") not in covered:
            proposal = proposal_base(series_cfg, filename, event, source, checked_at)
            proposal.update({
                "proposalType": "unresolved", "sessionId": session.get("id"),
                "sessionName": session.get("name"),
                "current": {"date": session.get("date"), "timeLocal": None, "durationMinutes": session.get("durationMinutes")},
                "sourceTime": None, "editorTimeZone": editor_timezone, "proposed": None,
                "dateChanged": False, "status": "unresolved",
                "reason": "No unambiguous matching session was found on the official source.",
            })
            proposal["fingerprint"] = fingerprint({
                "seriesId": proposal["seriesId"], "eventId": proposal["eventId"],
                "sessionId": proposal["sessionId"], "source": source.final_url, "proposed": None,
            })
            proposal["id"] = "schedule-" + proposal["fingerprint"][:16]
            proposals.append(proposal)
    return proposals


def merge_proposals(previous: Sequence[dict], current: Sequence[dict], generated_at: str) -> List[dict]:
    old_by_fp = {item.get("fingerprint"): item for item in previous}
    old_by_identity: Dict[str, List[dict]] = {}
    for item in previous:
        old_by_identity.setdefault(identity_key(item), []).append(item)
    merged: List[dict] = []
    current_fps = set()
    for item in current:
        previous_identity = old_by_identity.get(identity_key(item), [])
        if item.get("status") == "unresolved":
            last_reliable = next(
                (old_item for old_item in reversed(previous_identity)
                 if old_item.get("proposed") and old_item.get("status") in {"open", "accepted", "rejected"}
                 and (old_item.get("seriesId") != "f1" or old_item.get("source", {}).get("confirmationPolicy") == "f1-published-schedule-v1")
                 and (old_item.get("seriesId") != "bsb" or old_item.get("source", {}).get("confirmationPolicy") == "bsb-official-pdf-v1")),
                None,
            )
            if last_reliable:
                preserved = dict(last_reliable)
                preserved["stale"] = True
                preserved["lastFailedCheckAt"] = item.get("source", {}).get("checkedAt")
                preserved["lastScanError"] = item.get("reason")
                merged.append(preserved)
                current_fps.add(preserved.get("fingerprint"))
                continue
        current_fps.add(item["fingerprint"])
        old = old_by_fp.get(item["fingerprint"])
        if old and old.get("status") in {"accepted", "rejected"}:
            item["status"] = old["status"]
            if old.get("decisionAt"):
                item["decisionAt"] = old["decisionAt"]
        changed = [old_item for old_item in previous_identity if old_item.get("fingerprint") != item["fingerprint"]]
        if changed:
            item["replacesFingerprint"] = changed[-1].get("fingerprint")
        merged.append(item)
    for old in previous:
        if old.get("fingerprint") in current_fps:
            continue
        replacement = next((item for item in merged if item.get("replacesFingerprint") == old.get("fingerprint")), None)
        if replacement:
            superseded = dict(old)
            superseded["status"] = "superseded"
            superseded["supersededAt"] = generated_at
            superseded["supersededBy"] = replacement.get("fingerprint")
            merged.append(superseded)
    def chronological_key(item: dict) -> Tuple[str, str, str, str, bool]:
        proposed = item.get("proposed") or {}
        source_time = item.get("sourceTime") or {}
        current = item.get("current") or {}
        session_date = proposed.get("date") or source_time.get("date") or current.get("date") or "9999-99-99"
        session_time = proposed.get("timeLocal") or source_time.get("time") or current.get("timeLocal") or "99:99"
        return (
            session_date,
            session_time,
            item.get("seriesName") or "",
            item.get("eventName") or "",
            item.get("status") == "superseded",
        )

    return sorted(merged, key=chronological_key)


def nascar_scan_week(checked_at: datetime) -> Optional[Tuple[date, date]]:
    """Unlock only this Monday–Sunday race week at Monday noon in the Netherlands."""
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    local = checked_at.astimezone(ZoneInfo("Europe/Amsterdam"))
    monday = local.date() - timedelta(days=local.weekday())
    opens_at = datetime.combine(monday, time(12), tzinfo=ZoneInfo("Europe/Amsterdam"))
    if local < opens_at:
        return None
    return monday, monday + timedelta(days=6)


def nascar_event_in_week(event: dict, week: Optional[Tuple[date, date]]) -> bool:
    if week is None:
        return False
    sessions = event.get("sessions", [])
    # Prefer the race date: a stray practice date must not unlock a later race.
    races = [session for session in sessions if session.get("kind") == "race"]
    dates = []
    for session in races or sessions:
        try:
            dates.append(date.fromisoformat(str(session.get("date") or "")))
        except ValueError:
            return False
    return bool(dates) and all(week[0] <= day <= week[1] for day in dates)


def event_refresh_due(event: dict, checked_at: datetime) -> bool:
    """Recheck filled times during the month before their race weekend."""
    dates = []
    for session in event.get("sessions", []):
        try:
            dates.append(date.fromisoformat(str(session.get("date") or "")))
        except ValueError:
            continue
    if not dates:
        return False
    today = checked_at.astimezone(ZoneInfo("Europe/Berlin")).date()
    return 0 <= (max(dates) - today).days <= 30


def calendar_inventory(
    root: Path,
    scope: set,
    event_ids: Optional[set] = None,
    include_filled: bool = False,
    checked_at: Optional[datetime] = None,
) -> List[Tuple[str, dict]]:
    inventory: List[Tuple[str, dict]] = []
    nascar_week = nascar_scan_week(checked_at or datetime.now(timezone.utc))
    pattern = re.compile(r"^(.+)_([0-9]{4})\.json$")
    for path in sorted(root.glob("*_*.json")):
        match = pattern.match(path.name)
        if not match or match.group(1).endswith("_standings") or match.group(1) not in scope:
            continue
        try:
            rounds = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.error("Skipping invalid calendar %s: %s", path.name, exc)
            continue
        for event in rounds if isinstance(rounds, list) else []:
            if event_ids and event.get("id") not in event_ids:
                continue
            sessions = event.get("sessions", [])
            has_tbc = any(session.get("timeLocal") is None and not (event.get("seriesId") == "fd" and session.get("timeUTC")) for session in sessions)
            series_id = event.get("seriesId") or match.group(1)
            upcoming_incomplete_nascar = False
            if series_id in NASCAR_FEED_SERIES:
                # Apply before any fetch, including targeted and debug scans.
                if not nascar_event_in_week(event, nascar_week):
                    continue
                known_kinds = {session.get("kind") for session in sessions}
                upcoming_incomplete_nascar = not {"practice", "qualifying"}.issubset(known_kinds)
            if event_ids or include_filled or has_tbc or upcoming_incomplete_nascar or (
                (series_id in {"dtm", "sf", "supergt", "f1academy", "bsb", "fd"} or event.get("officialScheduleUrl")) and event_refresh_due(event, checked_at or datetime.now(timezone.utc))
            ):
                inventory.append((path.name, event))
    return inventory


def resolve_event_timezone(registry: dict, series_cfg: dict, event: dict) -> Optional[str]:
    configured = series_cfg.get("sourceTimeZone")
    if configured and configured != "track":
        return configured
    haystack = normalize("{} {} {}".format(event.get("raceName", ""), event.get("circuitName", ""), event.get("city", "")))
    matches = [(len(normalize(key)), zone) for key, zone in registry.get("eventTimeZones", {}).items() if normalize(key) in haystack]
    return max(matches)[1] if matches else None


def resolve_event_year(event: dict, fallback: int) -> int:
    for session in event.get("sessions", []):
        value = str(session.get("date") or "")
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
            return int(value[:4])
    return fallback


def fetch_manual_schedule(url: str, cfg: dict, year: int) -> FetchResult:
    """Use the exact configured event, never fall back to a different event."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise SourceError("Gebruik een HTTPS-eventlink zonder inloggegevens.")
    if not allowed_url(url, cfg["allowedDomains"]):
        raise SourceError("Het domein van deze eventlink is niet toegestaan als officiële bron voor deze serie. Controleer de link of laat het officiële domein toevoegen aan session-time-sources.json.")
    if cfg.get("sourceKind") == "motogp-api" and parsed.hostname in {"motogp.com", "www.motogp.com"}:
        match = re.fullmatch(r"/[^/]+/calendar/" + str(year) + r"/event/[^/]+/([^/]+)/?", parsed.path)
        if not match:
            raise SourceError("Deze MotoGP-link bevat geen herkenbare event-ID voor dit seizoen. Gebruik de volledige officiële eventpagina.")
        feed_url = "https://api.pulselive.motogp.com/motogp/v1/events?seasonYear={}".format(year)
        feed = fetch_url(feed_url, cfg["allowedDomains"], {"x-client": "FE", "x-referer-path": "/en/calendar", "Referer": url})
        payload = json_object(feed.body, "MotoGP events")
        matches = [item for item in payload if isinstance(item, dict) and str(item.get("id")) == match.group(1)] if isinstance(payload, list) else []
        if len(matches) != 1:
            raise SourceError("De event-ID uit de ingevulde MotoGP-link is niet eenduidig teruggevonden. Controleer de link en het seizoen.")
        selected = matches[0]
        return FetchResult(url, url, selected.get("name") or "Official MotoGP event", json.dumps(selected), feed.last_modified)
    target = url
    if cfg.get("sourceKind") == "dtm-api" and parsed.hostname != "api.dtm.com":
        match = re.fullmatch(r"/(?:[^/]+/)?events/([^/]+)/?", parsed.path)
        if not match or not match.group(1).endswith("-" + str(year)):
            raise SourceError("Gebruik de DTM-eventpagina van het juiste seizoen, bijvoorbeeld /mp/events/hockenheim-finale-2026.")
        target = "https://api.dtm.com/data?query=eventDetails&slug={}&lang=en".format(quote(match.group(1), safe="-"))
    is_pdf = urlparse(target).path.lower().endswith(".pdf") or "/document/download/" in target
    fetched = fetch_pdf_url(target, cfg["allowedDomains"], preserve_layout=cfg.get("sourceKind") == "elms-timetable") if is_pdf else fetch_url(target, cfg["allowedDomains"])
    if cfg.get("sourceKind") == "rally-itinerary" and not is_pdf:
        fetched = retry_rally_prerender(fetched, target, cfg["allowedDomains"])
    return FetchResult(url, fetched.final_url, fetched.title, fetched.body, fetched.last_modified)


def scan(root: Path, registry: dict, fixtures: Optional[Path], only_series: Optional[set], checked_at: str, fixtures_only: bool = False, only_events: Optional[set] = None, include_filled: bool = False, replace_filled: bool = False) -> dict:
    series_by_id = {item["seriesId"]: item for item in registry["series"]}
    scope = set(series_by_id)
    if only_series:
        scope &= only_series
    scan_instant = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    nascar_week = nascar_scan_week(scan_instant)
    inventory = calendar_inventory(root, scope, only_events, include_filled, scan_instant)
    LOG.info("Found %d in-scope events requiring an official-session check", len(inventory))
    proposals: List[dict] = []
    source_overview: Dict[str, dict] = {}
    calendar_cache: Dict[str, FetchResult] = {}
    for filename, event in inventory:
        series_id = event.get("seriesId") or filename.rsplit("_", 1)[0]
        cfg = series_by_id[series_id]
        year_match = re.search(r"_(\d{4})\.json$", filename)
        year = int(year_match.group(1))
        event_year = resolve_event_year(event, year)
        manual_url = str(event.get("officialScheduleUrl") or "").strip()
        stable_url = manual_url or cfg["startUrl"].replace("{year}", str(year))
        source_tz = resolve_event_timezone(registry, cfg, event)
        fixture = fixtures / "{}__{}.html".format(series_id, event.get("id")) if fixtures else None
        source = FetchResult(stable_url, stable_url, "Official source", "", None)
        source_candidates: List[FetchResult] = []
        try:
            if manual_url and not (fixture and fixture.exists()) and not fixtures_only:
                source = fetch_manual_schedule(manual_url, cfg, event_year)
                source_candidates = [source]
            elif fixture and fixture.exists():
                source = fixture_result(fixture, stable_url)
                source_candidates = [source]
            elif fixtures_only:
                raise SourceError("no fixed official fixture is available for this event")
            elif cfg.get("sourceKind") == "nascar-et":
                # nascar.com blocks server-side scanners with Cloudflare. NASCAR's
                # own public cache exposes the same weekend sessions without that
                # browser-only barrier. Keep the stable weekend URL as the visible
                # citation while reading the official machine-readable feed.
                feed_url = "https://cf.nascar.com/cacher/{}/race_list_basic.json".format(event_year)
                if feed_url not in calendar_cache:
                    calendar_cache[feed_url] = fetch_url(feed_url, cfg["allowedDomains"])
                feed = calendar_cache[feed_url]
                source = FetchResult(stable_url, stable_url, "NASCAR official weekend schedule", feed.body, feed.last_modified)
                source_candidates = [source]
            elif cfg.get("sourceKind") == "motogp-api":
                feed_url = "https://api.pulselive.motogp.com/motogp/v1/events?seasonYear={}".format(event_year)
                if feed_url not in calendar_cache:
                    calendar_cache[feed_url] = fetch_url(feed_url, cfg["allowedDomains"], {
                        "x-client": "FE", "x-referer-path": "/en/calendar", "Referer": stable_url,
                    })
                selected = select_motogp_event(calendar_cache[feed_url].body, event)
                public_url = "https://www.motogp.com/en/calendar/{}/event/{}/{}".format(
                    event_year, url_slug(selected.get("url") or selected.get("place")), selected.get("id")
                )
                source = FetchResult(
                    stable_url, public_url, selected.get("name") or "Official MotoGP schedule",
                    json.dumps(selected, ensure_ascii=False), calendar_cache[feed_url].last_modified,
                )
                source_candidates = [source]
            elif cfg.get("sourceKind") == "worldsbk-api":
                rounds_url = "https://api.pulselive.worldsbk.com/wsbk-events/v1/seasons/{}/rounds".format(event_year)
                api_headers = {"x-client": "FE", "x-referer-path": "/en/calendar", "Referer": stable_url}
                if rounds_url not in calendar_cache:
                    calendar_cache[rounds_url] = fetch_url(rounds_url, cfg["allowedDomains"], api_headers)
                selected_round, circuit = select_worldsbk_round(calendar_cache[rounds_url].body, event)
                round_code = (selected_round.get("attributes") or {}).get("source_id")
                sessions_url = "https://api.pulselive.worldsbk.com/wsbk-events/v1/seasons/{}/rounds/{}/sessions".format(event_year, round_code)
                sessions_feed = fetch_url(sessions_url, cfg["allowedDomains"], api_headers)
                sessions_payload = json_object(sessions_feed.body, "WorldSBK sessions")
                source = FetchResult(
                    stable_url, stable_url,
                    (selected_round.get("attributes") or {}).get("description") or "Official WorldSBK schedule",
                    json.dumps({
                        "round": selected_round, "circuit": circuit,
                        "sessions": sessions_payload.get("data", []) if isinstance(sessions_payload, dict) else [],
                    }, ensure_ascii=False), sessions_feed.last_modified,
                )
                source_candidates = [source]
            else:
                known = registry.get("knownEventUrls", {}).get("{}:{}".format(series_id, event.get("id")))
                if known:
                    source = fetch_url(known, cfg["allowedDomains"])
                    if cfg.get("sourceKind") == "rally-itinerary":
                        source = retry_rally_prerender(source, known, cfg["allowedDomains"])
                    source = FetchResult(stable_url, source.final_url, source.title, source.body, source.last_modified)
                    source_candidates = [source]
                else:
                    event_urls: List[str] = []
                    if cfg.get("eventUrlTemplate"):
                        default_slug = formula_e_slug(event.get("raceName")) if cfg.get("sourceKind") == "formula-e-schedule" else url_slug(event.get("raceName"))
                        slug = cfg.get("eventSlugAliases", {}).get(normalize(event.get("raceName")), default_slug)
                        templates = [cfg["eventUrlTemplate"]] + list(cfg.get("eventUrlFallbackTemplates", []))
                        event_urls = [
                            template.replace("{year}", str(event_year))
                            .replace("{season}", formula_e_season(year))
                            .replace("{round}", str(event.get("roundNumber") or ""))
                            .replace("{slug}", slug)
                            for template in templates
                        ]
                    else:
                        calendar_urls = [cfg.get("calendarUrl", stable_url)] + list(cfg.get("startUrlFallbacks", []))
                        calendar_urls = [url.replace("{year}", str(year)) for url in calendar_urls]
                        calendar = None
                        calendar_errors = []
                        for calendar_url in calendar_urls:
                            try:
                                if calendar_url not in calendar_cache:
                                    fetched_calendar = fetch_url(calendar_url, cfg["allowedDomains"])
                                    if cfg.get("sourceKind") == "rally-itinerary":
                                        fetched_calendar = retry_rally_prerender(fetched_calendar, calendar_url, cfg["allowedDomains"])
                                    calendar_cache[calendar_url] = fetched_calendar
                                calendar = calendar_cache[calendar_url]
                                break
                            except SourceError as exc:
                                calendar_errors.append(str(exc))
                        if calendar is None:
                            raise SourceError(calendar_errors[-1] if calendar_errors else "could not read official calendar")
                        if cfg.get("sourceKind") == "f1academy-schedule":
                            discovered = discover_academy_event_url(calendar, event, event_year)
                        elif cfg.get("sourceKind") in {"superformula-timetable", "supergt-timetable"}:
                            discovered = discover_japanese_event_url(calendar, event, event_year, series_id)
                        elif cfg.get("sourceKind") == "formula1-jsonld":
                            discovered = discover_formula1_event_url(calendar, event, event_year)
                        elif cfg.get("sourceKind") == "btcc-timetable":
                            discovered = discover_btcc_event_url(calendar, event)
                        else:
                            discovered = discover_event_url(calendar, event, year)
                        event_urls = [discovered] if discovered else []
                    if event_urls:
                        fetch_errors = []
                        for event_url in event_urls:
                            try:
                                event_is_pdf = bool(urlparse(event_url).path.lower().endswith(".pdf") or "/document/download/" in event_url)
                                fetched = fetch_pdf_url(event_url, cfg["allowedDomains"]) if event_is_pdf else fetch_url(event_url, cfg["allowedDomains"])
                                if cfg.get("sourceKind") == "rally-itinerary" and not event_is_pdf:
                                    fetched = retry_rally_prerender(fetched, event_url, cfg["allowedDomains"])
                                candidate = FetchResult(stable_url, fetched.final_url, fetched.title, fetched.body, fetched.last_modified)
                                source_candidates.append(candidate)
                            except SourceError as exc:
                                fetch_errors.append(str(exc))
                        if not source_candidates and fetch_errors:
                            raise SourceError(fetch_errors[-1])
                        source = source_candidates[0]
                    else:
                        source = calendar
                        source_candidates = [source]
            if not allowed_url(source.final_url, cfg["allowedDomains"]):
                raise SourceError("final source URL is not official: {}".format(source.final_url))
            source_title_years = set(re.findall(r"\b20\d{2}\b", source.title))
            expected_years = {str(year), str(event_year)}
            if source_title_years and source_title_years.isdisjoint(expected_years):
                raise SourceError(
                    "official event page is for {} rather than calendar year {}".format(
                        ", ".join(sorted(source_title_years)), event_year
                    )
                )
            if manual_url and cfg["sourceKind"] in {"motogp-api", "worldsbk-api"} and not source.body.lstrip().startswith("{"):
                raise SourceError("Deze eventpagina levert geen rechtstreeks leesbaar tijdschema. De website gebruikt mogelijk JavaScript of is gewijzigd. Controleer de bronlink; er is niet uitgeweken naar een ander event.")
            if cfg["sourceKind"] == "motogp-api":
                source_tz = motogp_source_timezone(source) or source_tz
            elif cfg["sourceKind"] == "worldsbk-api":
                source_tz = worldsbk_source_timezone(source) or source_tz
            if not source_tz:
                raise SourceError("no verified IANA track timezone is registered for this event")
            if manual_url and source.title == "Official timetable PDF":
                if cfg["sourceKind"] == "elms-timetable":
                    sessions = parse_elms_timetable(source, event, event_year, source_tz)
                elif cfg["sourceKind"] == "british-gt-pdf":
                    sessions = parse_british_gt_pdf(source, event_year, source_tz)
                else:
                    sessions = parse_official_schedule_document(source, event_year, source_tz, cfg.get("documentCategories", [cfg["name"]]))
            elif cfg["sourceKind"] == "nascar-et":
                sessions = parse_nascar_feed(source, event, event_year, source_tz) if source.body.lstrip().startswith("{") else parse_nascar_text(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "elms-timetable":
                pdf_url = discover_elms_timetable_pdf(source)
                if not pdf_url:
                    raise SourceError("The official ELMS event page has no published timetable yet")
                source = fetch_pdf_url(pdf_url, cfg["allowedDomains"], preserve_layout=True)
                sessions = parse_elms_timetable(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "british-gt-pdf":
                pdf_url = discover_timetable_pdf(source)
                if not pdf_url:
                    raise SourceError("op de officiële eventpagina is nog geen timetable-PDF gepubliceerd")
                source = fetch_pdf_url(pdf_url, cfg["allowedDomains"])
                sessions = parse_british_gt_pdf(source, event_year, source_tz)
            elif cfg["sourceKind"] == "24hseries-timetable":
                sessions = parse_24hseries_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "dtm-api":
                sessions = parse_dtm_api(source, event_year, source_tz)
                calendar_dates = {item.get("date") for item in event.get("sessions", []) if item.get("date")}
                if sessions and calendar_dates and not any(item.date in calendar_dates for item in sessions):
                    raise SourceError("DTM timetable dates do not match the selected calendar event")
            elif cfg["sourceKind"] == "formula-e-schedule":
                sessions = parse_formula_e_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "imsa-event-schedule":
                sessions = parse_imsa_event_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "superformula-timetable":
                sessions = parse_superformula_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "supergt-timetable":
                sessions = parse_supergt_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "bsb-timetable":
                calendar_url = cfg["calendarUrl"]
                if calendar_url not in calendar_cache:
                    calendar_cache[calendar_url] = fetch_url(calendar_url, cfg["allowedDomains"])
                pdf_url = discover_timetable_pdf(source)
                if not pdf_url:
                    raise SourceError("BSB: official timetable PDF is not published; HTML corrections are not safe")
                document = fetch_pdf_url(pdf_url, cfg["allowedDomains"], preserve_layout=True)
                sessions = parse_bsb_pdf(document, calendar_cache[calendar_url], event, event_year, source.final_url)
                source = document
            elif cfg["sourceKind"] == "fd-timetable":
                sessions = parse_fd_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "supercars-embedded-schedule":
                sessions = parse_supercars_schedule(source, event_year, source_tz, cfg["officialSeriesName"])
            elif cfg["sourceKind"] == "motogp-api":
                sessions = parse_motogp_schedule(source, cfg["officialCategory"], source_tz)
            elif cfg["sourceKind"] == "worldsbk-api":
                sessions = parse_worldsbk_schedule(source, cfg["officialCategoryId"], source_tz)
            elif cfg["sourceKind"] == "formula1-jsonld":
                sessions = parse_formula1_schedule(source)
            elif cfg["sourceKind"] == "f1academy-schedule":
                sessions = parse_academy_schedule(source, event, event_year, source_tz)
            elif cfg["sourceKind"] == "fia-track-time":
                sessions = (
                    parse_fia_track_schedule(source, source_tz)
                    or parse_official_tables(source, event_year, source_tz)
                )
            elif cfg["sourceKind"] == "btcc-timetable":
                sessions = parse_btcc_timetable(source, event_year, source_tz)
            elif cfg["sourceKind"] == "rally-itinerary":
                sessions = parse_rally_itinerary(source, event_year, source_tz)
                itinerary_url = discover_rally_itinerary_url(source)
                if not sessions and itinerary_url:
                    if urlparse(itinerary_url).path.lower().endswith(".pdf"):
                        itinerary = fetch_pdf_url(itinerary_url, cfg["allowedDomains"])
                    else:
                        itinerary = fetch_url(itinerary_url, cfg["allowedDomains"])
                        itinerary = retry_rally_prerender(itinerary, itinerary_url, cfg["allowedDomains"])
                    parsed = parse_rally_itinerary(itinerary, event_year, source_tz)
                    if parsed:
                        source, sessions = itinerary, parsed
                if not sessions:
                    pdf_url = discover_timetable_pdf(source)
                    if pdf_url:
                        itinerary = fetch_pdf_url(pdf_url, cfg["allowedDomains"])
                        parsed = parse_rally_itinerary(itinerary, event_year, source_tz)
                        if parsed:
                            source, sessions = itinerary, parsed
            elif cfg["sourceKind"] == "sro-event-timetable":
                sessions = (
                    parse_official_tables(source, event_year, source_tz)
                    if cfg.get("preferHtmlTimetable") else []
                )
                pdf_url = discover_timetable_pdf(source) if not sessions else None
                if pdf_url:
                    try:
                        document = fetch_pdf_url(pdf_url, cfg["allowedDomains"])
                        categories = cfg.get("eventDocumentCategories", {}).get(
                            event.get("id"), cfg.get("documentCategories", [cfg["name"]])
                        )
                        sessions = parse_official_schedule_document(document, event_year, source_tz, categories)
                        if sessions:
                            source = document
                    except SourceError as exc:
                        LOG.info("Official SRO PDF could not be used for %s: %s", event.get("id"), exc)
                if not sessions:
                    sessions = parse_official_tables(source, event_year, source_tz)
            elif cfg["sourceKind"] == "official-schedule-document":
                sessions = []
                document_candidates = source_candidates or [source]
                for candidate in document_candidates:
                    document, parsed = candidate, []
                    pdf_url = discover_timetable_pdf(candidate) if candidate.title != "Official timetable PDF" else None
                    if pdf_url:
                        try:
                            document = fetch_pdf_url(pdf_url, cfg["allowedDomains"])
                            parsed = parse_official_schedule_document(document, event_year, source_tz, cfg.get("documentCategories", [cfg["name"]]))
                        except SourceError as exc:
                            LOG.info("Official PDF could not be used for %s: %s", event.get("id"), exc)
                    if not parsed:
                        calendar_url = discover_event_calendar(candidate)
                        if calendar_url:
                            try:
                                calendar_source = fetch_url(calendar_url, cfg["allowedDomains"])
                                calendar_source = FetchResult(stable_url, calendar_source.final_url, "Official event calendar", calendar_source.body, calendar_source.last_modified)
                                calendar_sessions = parse_ical_sessions(calendar_source, event_year, source_tz)
                                if calendar_sessions:
                                    document, parsed = calendar_source, calendar_sessions
                            except SourceError as exc:
                                LOG.info("Official event calendar could not be used for %s: %s", event.get("id"), exc)
                    if not parsed:
                        document = candidate
                        parsed = parse_official_schedule_document(document, event_year, source_tz, cfg.get("documentCategories", [cfg["name"]]))
                    if parsed:
                        source, sessions = document, parsed
                        break
            else:
                sessions = parse_official_tables(source, event_year, source_tz)
            if series_id in NASCAR_FEED_SERIES:
                # A rolling source can still show the previous weekend. Never
                # turn those dates (or future dates) into this week's proposals.
                sessions = [item for item in sessions if nascar_week and
                            nascar_week[0].isoformat() <= item.date <= nascar_week[1].isoformat()]
            if not sessions:
                raise SourceError("De bron bevat geen betrouwbaar leesbare sessietijden. Het tijdschema is mogelijk nog niet gepubliceerd of de website is gewijzigd. Controleer de eventlink en het officiële tijdschema.")
            if manual_url and cfg["sourceKind"] != "f1academy-schedule":
                calendar_dates = sorted({str(item.get("date")) for item in event.get("sessions", []) if item.get("date")})
                if not calendar_dates or not any(item.date in calendar_dates for item in sessions):
                    raise SourceError("De gevonden datums passen niet bij dit event, of de eventdatums ontbreken. Controleer de eventlink en de datums in Agenda.")
                first_date, last_date = calendar_dates[0], calendar_dates[-1]
                if cfg["sourceKind"] == "supercars-embedded-schedule":
                    alias = cfg.get("eventSlugAliases", {}).get(normalize(event.get("raceName", "")))
                    expected_path = "/events/{}-{}/schedule".format(event_year, alias) if alias else None
                    if expected_path and urlparse(source.final_url).path.rstrip("/") == expected_path:
                        first_date = (date.fromisoformat(first_date) - timedelta(days=1)).isoformat()
                if any(item.date < first_date or item.date > last_date for item in sessions):
                    raise SourceError("De bron bevat sessies buiten de datums van dit event. De koppeling is onzeker; controleer de link en eventdatums.")
            editor_tz = registry.get("editorTimeZones", {}).get(series_id, registry["editorTimeZones"]["default"])
            if series_id == "fd" and year == 2026:
                editor_tz = "UTC"  # Published 2026 calendar stores UTC clocks.
            refresh_filled_source = (series_id in {"dtm", "sf", "supergt", "f1academy", "bsb", "fd"} or bool(manual_url)) and event_refresh_due(event, scan_instant)
            event_proposals = build_event_proposals(
                cfg, filename, event, source, sessions, editor_tz, checked_at,
                include_filled, replace_filled or (refresh_filled_source and not include_filled),
            )
            proposals.extend(event_proposals)
            uncertain = any(item.get("status") == "requires_review" for item in event_proposals)
            unmatched = False
            if manual_url or cfg["sourceKind"] == "f1academy-schedule":
                covered = set()
                for official in sessions:
                    matched, conflict = (match_academy_session(official, sessions, event.get("sessions", []))
                                         if cfg["sourceKind"] == "f1academy-schedule"
                                         else match_session(official, event.get("sessions", [])))
                    if cfg["sourceKind"] == "fd-timetable":
                        matched, conflict = match_fd(official, sessions, event.get("sessions", []), event.get("sessions", []))
                    if matched and not conflict:
                        covered.add(matched.get("id"))
                unmatched = any(normalize_kind(item.get("name", "")) and item.get("id") not in covered for item in event.get("sessions", []))
            uncertain = uncertain or unmatched
            source_overview["{}:{}".format(series_id, event.get("id"))] = {
                "seriesId": series_id, "eventId": event.get("id"), "eventName": event.get("raceName"),
                "calendarFile": filename, "manualUrl": manual_url,
                "stableUrl": stable_url, "finalUrl": source.final_url, "title": source.title,
                "checkedAt": checked_at, "lastModified": source.last_modified,
                "status": "requires_review" if uncertain else "ok",
                "reason": ("Niet alle bestaande sessies zijn op deze bron herkend. Controleer het tijdschema; alleen herkenbare sessies leveren voorstellen op." if unmatched else "De koppeling van een of meer sessies is onzeker. Beoordeel de conflicten bij Voorstellen.") if uncertain else "",
            }
        except SourceError as exc:
            LOG.warning("%s / %s: %s", series_id, event.get("id"), exc)
            proposals.extend(unresolved_proposals(cfg, filename, event, source, checked_at, str(exc)))
            source_overview["{}:{}".format(series_id, event.get("id"))] = {
                "seriesId": series_id, "eventId": event.get("id"), "eventName": event.get("raceName"),
                "calendarFile": filename, "manualUrl": manual_url,
                "stableUrl": stable_url, "finalUrl": source.final_url, "title": source.title,
                "checkedAt": checked_at, "lastModified": source.last_modified, "status": "unresolved", "reason": str(exc),
            }
    return {
        "schemaVersion": PROPOSAL_SCHEMA_VERSION,
        "generatedAt": checked_at,
        "proposals": proposals,
        "sourcesUsed": sorted(source_overview.values(), key=lambda item: (item["seriesId"], item["eventName"] or "")),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixtures-dir", type=Path, help="Read fixed official HTML fixtures instead of the network when available")
    parser.add_argument("--fixtures-only", action="store_true", help="Never use the network when a requested fixture is absent")
    parser.add_argument("--series", action="append", help="Limit to an in-scope RaceDay series id (repeatable)")
    parser.add_argument("--event", action="append", help="Limit to a specific RaceDay event id (repeatable)")
    parser.add_argument("--include-filled", action="store_true", help="Debug: compare already-filled sessions without making them actionable")
    parser.add_argument("--replace-filled", action="store_true", help="Event scan: propose official corrections for already-filled sessions")
    parser.add_argument("--now", help="Fixed ISO timestamp for deterministic runs")
    parser.add_argument("--dry-run", action="store_true", help="Print the result without writing the proposal store")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    registry_path = (args.registry or root / "session-time-sources.json").resolve()
    output_path = (args.output or root / ".raceday" / "session-time-proposals.json").resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    checked_at = args.now or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    result = scan(root, registry, args.fixtures_dir, set(args.series or []), checked_at, args.fixtures_only, set(args.event or []), args.include_filled, args.replace_filled)
    previous = []
    previous_sources = []
    if output_path.exists():
        try:
            previous_store = json.loads(output_path.read_text(encoding="utf-8"))
            previous = previous_store.get("proposals", [])
            previous_sources = previous_store.get("sourcesUsed", [])
        except (OSError, json.JSONDecodeError):
            LOG.warning("Existing proposal store is invalid; rebuilding it")
    # A targeted scan owns only its selected series/events. Preserve all
    # other decisions and proposals instead of deleting them from the store.
    in_scope = lambda item: (not args.series or item.get("seriesId") in args.series) and (not args.event or item.get("eventId") in args.event)
    untouched = [item for item in previous if not in_scope(item)]
    result["proposals"] = untouched + merge_proposals([item for item in previous if in_scope(item)], result["proposals"], checked_at)
    result["sourcesUsed"] = [item for item in previous_sources if not in_scope(item)] + result.get("sourcesUsed", [])
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(rendered, end="")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        old = output_path.read_text(encoding="utf-8") if output_path.exists() else None
        if old != rendered:
            output_path.write_text(rendered, encoding="utf-8")
            LOG.info("Wrote %d proposals to %s", len(result["proposals"]), output_path)
        else:
            LOG.info("Proposal store is unchanged")
    actionable = sum(item.get("status") == "open" for item in result["proposals"])
    unresolved = sum(item.get("status") in {"unresolved", "requires_review"} for item in result["proposals"])
    LOG.info("%d actionable, %d need manual review", actionable, unresolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
