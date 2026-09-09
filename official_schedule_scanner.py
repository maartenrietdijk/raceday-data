#!/usr/bin/env python3
"""Build reviewable RaceDay session-time proposals from official sources only.

The scanner never edits a calendar JSON file. It reads only series that contain
TBC sessions and writes its idempotent result to
`.raceday/session-time-proposals.json` for review in raceday-editor.html.
"""

from __future__ import annotations

import argparse
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
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG = logging.getLogger("raceday.session_times")
USER_AGENT = "RaceDay official schedule scanner/1.0 (+https://raceday.app)"
PROPOSAL_SCHEMA_VERSION = 1


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


def tokens(value: object) -> set:
    return {part for part in normalize(value).split() if len(part) > 1}


def similarity(left: object, right: object) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def parse_clock(value: str) -> str:
    raw = html.unescape(value).strip().lower().replace(".", "")
    match = re.search(r"\b(\d{1,2})[:.]?(\d{2})\s*([ap]m)?\b", raw)
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


def fetch_url(url: str, allowed_domains: Sequence[str]) -> FetchResult:
    if not allowed_url(url, allowed_domains):
        raise SourceError("source domain is not allowlisted: {}".format(url))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=25, context=tls_context()) as response:
            final_url = response.geturl()
            if not allowed_url(final_url, allowed_domains):
                raise SourceError("redirect left official allowlist: {}".format(final_url))
            body = response.read(8_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
            return FetchResult(
                stable_url=url,
                final_url=final_url,
                title=page_title(body),
                body=body,
                last_modified=response.headers.get("Last-Modified"),
            )
    except URLError as exc:
        # Some older official SRO servers omit an intermediate certificate that
        # Xcode's Python cannot build, while the platform curl trust stack can.
        # Curl still performs full certificate verification; never use -k.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return fetch_url_with_curl(url, allowed_domains)
        raise SourceError("could not read official source {}: {}".format(url, exc)) from exc
    except (HTTPError, TimeoutError) as exc:
        raise SourceError("could not read official source {}: {}".format(url, exc)) from exc


def fetch_url_with_curl(url: str, allowed_domains: Sequence[str]) -> FetchResult:
    marker = "\nRACEDAY_FINAL_URL:"
    try:
        result = subprocess.run(
            ["curl", "--fail", "--location", "--max-time", "25", "--max-filesize", "8000000",
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


def extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SourceError("PDF-uitlezer ontbreekt; installeer pypdf") from exc
    try:
        pages = [(page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages]
    except Exception as exc:
        raise SourceError("officiële timetable-PDF kon niet worden uitgelezen: {}".format(exc)) from exc
    text = "\f".join(pages)
    if not text.strip():
        raise SourceError("officiële timetable-PDF bevat geen uitleesbare tekst")
    return text


def fetch_pdf_url(url: str, allowed_domains: Sequence[str]) -> FetchResult:
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
            return FetchResult(url, final_url, "Official timetable PDF", extract_pdf_text(data), response.headers.get("Last-Modified"))
    except URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
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
    return FetchResult(url, final_url, "Official timetable PDF", extract_pdf_text(data), None)


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


def discover_event_url(calendar: FetchResult, event: dict, year: int) -> Optional[str]:
    target = "{} {} {} {}".format(
        event.get("raceName", ""), event.get("circuitName", ""), event.get("city", ""), year
    )
    best: Tuple[float, Optional[str]] = (0.0, None)
    for url, label in extract_links(calendar.body, calendar.final_url):
        if not re.search(r"event|racing|schedule|timetable", url, re.I):
            continue
        score = similarity(target, "{} {}".format(label, url.replace("-", " ")))
        if score > best[0]:
            best = (score, url)
    return best[1] if best[0] >= 0.14 else None


def discover_timetable_pdf(event_page: FetchResult) -> Optional[str]:
    candidates = []
    for url, label in extract_links(event_page.body, event_page.final_url):
        if urlparse(url).path.lower().endswith(".pdf"):
            priority = 2 if re.search(r"timetable|schedule|programme", "{} {}".format(label, url), re.I) else 1
            candidates.append((priority, url))
    return max(candidates, default=(0, None))[1]


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
    if "sprint qualifying" in raw or "sprint shootout" in raw:
        return "sprintQualifying"
    if "sprint" in raw and "race" in raw:
        return "sprintRace"
    if "feature" in raw and "race" in raw:
        return "featureRace"
    if "hyperpole" in raw:
        return "hyperpole"
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
        local_col = time_candidates[0] if time_candidates else (1 if len(headers) > 1 else None)
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


def parse_nascar_text(fetch: FetchResult, event: dict, year: int, source_timezone: str) -> List[SourceSession]:
    text = strip_tags(fetch.body)
    target_terms = [event.get("raceName", ""), event.get("circuitName", "")]
    positions = [normalize(text).find(normalize(term)) for term in target_terms if term]
    positions = [position for position in positions if position >= 0]
    window = text
    if positions:
        # Work on the original text around the first target occurrence when possible.
        needle = next((term for term in target_terms if term and re.search(re.escape(term), text, re.I)), None)
        if needle:
            match = re.search(re.escape(needle), text, re.I)
            window = text[max(0, match.start() - 400):match.end() + 700]
    date_match = re.search(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*[.,]?\s+"
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
        r"(\d{1,2})(?:,\s*|\s+)(\d{4})",
        window, re.I,
    ) or re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", window)
    time_match = re.search(r"\b(\d{1,2}[:.]\d{2}\s*[AP]M)\s*(?:ET|EST|EDT)\b", window, re.I)
    if not date_match or not time_match:
        return []
    if date_match.group(1).isdigit():
        session_date = date(int(date_match.group(3)), int(date_match.group(1)), int(date_match.group(2))).isoformat()
    else:
        session_date = date(int(date_match.group(3)), MONTHS[date_match.group(1).lower()], int(date_match.group(2))).isoformat()
    return [SourceSession("Race", session_date, parse_clock(time_match.group(1)), source_timezone)]


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


def strict_source_instant(session: SourceSession) -> datetime:
    try:
        zone = ZoneInfo(session.timezone)
    except ZoneInfoNotFoundError as exc:
        raise SourceError("unknown IANA timezone {}".format(session.timezone)) from exc
    naive = datetime.fromisoformat("{}T{}:00".format(session.date, session.local_time))
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
    return next(iter(unique.values()))


def editor_value(session: SourceSession, editor_timezone: str) -> Tuple[str, str, str]:
    instant = strict_source_instant(session)
    editor = instant.astimezone(ZoneInfo(editor_timezone))
    return editor.strftime("%Y-%m-%d"), editor.strftime("%H:%M"), instant.isoformat().replace("+00:00", "Z")


def session_number(name: str) -> Optional[int]:
    match = re.search(r"\b(\d+)\b", normalize(name))
    return int(match.group(1)) if match else None


def match_session(source: SourceSession, sessions: Sequence[dict]) -> Tuple[Optional[dict], Optional[str]]:
    source_kind = normalize_kind(source.name)
    if source_kind not in SUPPORTED_KINDS:
        return None, "unknown official session type"
    exact = [item for item in sessions if normalize(item.get("name")) == normalize(source.name)]
    if len(exact) == 1:
        return exact[0], None
    source_number = session_number(source.name)
    numbered = [
        item for item in sessions
        if item.get("kind") == source_kind and session_number(item.get("name", "")) == source_number
    ]
    if source_number is not None and len(numbered) == 1:
        return numbered[0], None
    same_kind = [item for item in sessions if item.get("kind") == source_kind]
    if source_number is not None and not numbered:
        generic = [item for item in same_kind if session_number(item.get("name", "")) is None]
        if len(generic) == 1:
            return generic[0], "official source has numbered {} sessions but the editor has one unnumbered session".format(source_kind)
        return None, None
    if len(same_kind) == 1 and source_number is None:
        if tokens(source.name) & tokens(same_kind[0].get("name", "")):
            return same_kind[0], None
        return None, None
    if same_kind:
        return None, "multiple {} sessions make automatic matching ambiguous".format(source_kind)
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
        },
    }


def unresolved_proposals(series_cfg: dict, filename: str, event: dict, source: FetchResult, checked_at: str, reason: str) -> List[dict]:
    proposals = []
    for session in event.get("sessions", []):
        if session.get("timeLocal") is not None:
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
) -> List[dict]:
    proposals: List[dict] = []
    existing = event.get("sessions", [])
    matched_ids = set()
    for official in official_sessions:
        kind = normalize_kind(official.name)
        if not kind:
            continue
        matched, conflict = match_session(official, existing)
        if matched and matched.get("timeLocal") is not None:
            matched_ids.add(matched.get("id"))
            continue
        try:
            proposed_date, proposed_time, utc_instant = editor_value(official, editor_timezone)
        except SourceError as exc:
            conflict = str(exc)
            proposed_date = proposed_time = utc_instant = None
        proposal = proposal_base(series_cfg, filename, event, source, checked_at)
        proposal.update({
            "proposalType": "conflict" if conflict else ("time-update" if matched else "new-session"),
            "sessionId": matched.get("id") if matched else None,
            "sessionName": matched.get("name") if matched else official.name,
            "current": {
                "date": matched.get("date") if matched else None,
                "timeLocal": matched.get("timeLocal") if matched else None,
                "durationMinutes": matched.get("durationMinutes") if matched else None,
            },
            "sourceTime": {"date": official.date, "time": official.local_time, "timeZone": official.timezone},
            "editorTimeZone": editor_timezone,
            "proposed": None if conflict else {
                "date": proposed_date,
                "timeLocal": proposed_time,
                "durationMinutes": official.duration_minutes or (matched or {}).get("durationMinutes") or 60,
                "name": official.name,
                "kind": kind,
                "utcInstant": utc_instant,
                "sessionId": (matched or {}).get("id") or "{}-s{}".format(event.get("id"), len(existing) + len(proposals) + 1),
            },
            "dateChanged": bool(matched and proposed_date and matched.get("date") != proposed_date),
            "status": "requires_review" if conflict else "open",
            "reason": conflict or "Official session time is available.",
        })
        fp_data = {
            "seriesId": proposal["seriesId"], "eventId": proposal["eventId"],
            "sessionId": proposal["sessionId"], "source": source.final_url,
            "sourceTime": proposal["sourceTime"], "proposed": proposal["proposed"],
        }
        proposal["fingerprint"] = fingerprint(fp_data)
        proposal["id"] = "schedule-" + proposal["fingerprint"][:16]
        proposals.append(proposal)
        if matched:
            matched_ids.add(matched.get("id"))

    # Every TBC session must either get a proposal or an explicit unresolved record.
    covered = {p.get("sessionId") for p in proposals if p.get("sessionId")}
    for session in existing:
        if session.get("timeLocal") is None and session.get("id") not in covered:
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
                 if old_item.get("proposed") and old_item.get("status") in {"open", "accepted", "rejected"}),
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
    return sorted(merged, key=lambda item: (item.get("eventName") or "", item.get("seriesName") or "", item.get("sessionName") or "", item.get("status") == "superseded"))


def calendar_inventory(root: Path, scope: set, event_ids: Optional[set] = None) -> List[Tuple[str, dict]]:
    inventory: List[Tuple[str, dict]] = []
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
            if event_ids or any(session.get("timeLocal") is None for session in event.get("sessions", [])):
                inventory.append((path.name, event))
    return inventory


def resolve_event_timezone(registry: dict, series_cfg: dict, event: dict) -> Optional[str]:
    configured = series_cfg.get("sourceTimeZone")
    if configured and configured != "track":
        return configured
    haystack = normalize("{} {} {}".format(event.get("raceName", ""), event.get("circuitName", ""), event.get("city", "")))
    matches = [(len(normalize(key)), zone) for key, zone in registry.get("eventTimeZones", {}).items() if normalize(key) in haystack]
    return max(matches)[1] if matches else None


def scan(root: Path, registry: dict, fixtures: Optional[Path], only_series: Optional[set], checked_at: str, fixtures_only: bool = False, only_events: Optional[set] = None) -> dict:
    series_by_id = {item["seriesId"]: item for item in registry["series"]}
    scope = set(series_by_id)
    if only_series:
        scope &= only_series
    inventory = calendar_inventory(root, scope, only_events)
    LOG.info("Found %d in-scope events with TBC sessions", len(inventory))
    proposals: List[dict] = []
    source_overview: Dict[str, dict] = {}
    calendar_cache: Dict[str, FetchResult] = {}
    for filename, event in inventory:
        series_id = event.get("seriesId") or filename.rsplit("_", 1)[0]
        cfg = series_by_id[series_id]
        year_match = re.search(r"_(\d{4})\.json$", filename)
        year = int(year_match.group(1))
        stable_url = cfg["startUrl"].replace("{year}", str(year))
        source_tz = resolve_event_timezone(registry, cfg, event)
        fixture = fixtures / "{}__{}.html".format(series_id, event.get("id")) if fixtures else None
        source = FetchResult(stable_url, stable_url, "Official source", "", None)
        try:
            if fixture and fixture.exists():
                source = fixture_result(fixture, stable_url)
            elif fixtures_only:
                raise SourceError("no fixed official fixture is available for this event")
            else:
                known = registry.get("knownEventUrls", {}).get("{}:{}".format(series_id, event.get("id")))
                if known:
                    source = fetch_url(known, cfg["allowedDomains"])
                    source = FetchResult(stable_url, source.final_url, source.title, source.body, source.last_modified)
                else:
                    calendar_url = cfg.get("calendarUrl", stable_url).replace("{year}", str(year))
                    if calendar_url not in calendar_cache:
                        calendar_cache[calendar_url] = fetch_url(calendar_url, cfg["allowedDomains"])
                    calendar = calendar_cache[calendar_url]
                    event_url = discover_event_url(calendar, event, year)
                    source = fetch_url(event_url, cfg["allowedDomains"]) if event_url else calendar
                    source = FetchResult(stable_url, source.final_url, source.title, source.body, source.last_modified)
            if not allowed_url(source.final_url, cfg["allowedDomains"]):
                raise SourceError("final source URL is not official: {}".format(source.final_url))
            source_title_years = set(re.findall(r"\b20\d{2}\b", source.title))
            if source_title_years and str(year) not in source_title_years:
                raise SourceError(
                    "official event page is for {} rather than calendar year {}".format(
                        ", ".join(sorted(source_title_years)), year
                    )
                )
            if not source_tz:
                raise SourceError("no verified IANA track timezone is registered for this event")
            if cfg["sourceKind"] == "nascar-et":
                sessions = parse_nascar_text(source, event, year, source_tz)
            elif cfg["sourceKind"] == "british-gt-pdf":
                pdf_url = discover_timetable_pdf(source)
                if not pdf_url:
                    raise SourceError("op de officiële eventpagina is nog geen timetable-PDF gepubliceerd")
                source = fetch_pdf_url(pdf_url, cfg["allowedDomains"])
                sessions = parse_british_gt_pdf(source, year, source_tz)
            else:
                sessions = parse_official_tables(source, year, source_tz)
            if not sessions:
                raise SourceError("official page contains no reliably parseable race sessions yet")
            editor_tz = registry.get("editorTimeZones", {}).get(series_id, registry["editorTimeZones"]["default"])
            proposals.extend(build_event_proposals(cfg, filename, event, source, sessions, editor_tz, checked_at))
            source_overview["{}:{}".format(series_id, event.get("id"))] = {
                "seriesId": series_id, "eventId": event.get("id"), "eventName": event.get("raceName"),
                "stableUrl": stable_url, "finalUrl": source.final_url, "title": source.title,
                "checkedAt": checked_at, "lastModified": source.last_modified,
            }
        except SourceError as exc:
            LOG.warning("%s / %s: %s", series_id, event.get("id"), exc)
            proposals.extend(unresolved_proposals(cfg, filename, event, source, checked_at, str(exc)))
            source_overview["{}:{}".format(series_id, event.get("id"))] = {
                "seriesId": series_id, "eventId": event.get("id"), "eventName": event.get("raceName"),
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
    result = scan(root, registry, args.fixtures_dir, set(args.series or []), checked_at, args.fixtures_only, set(args.event or []))
    previous = []
    if output_path.exists():
        try:
            previous = json.loads(output_path.read_text(encoding="utf-8")).get("proposals", [])
        except (OSError, json.JSONDecodeError):
            LOG.warning("Existing proposal store is invalid; rebuilding it")
    result["proposals"] = merge_proposals(previous, result["proposals"], checked_at)
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
