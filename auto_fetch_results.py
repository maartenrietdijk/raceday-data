#!/usr/bin/env python3
"""Automatically discover and refresh Motorsport.com session results.

The existing fetch_results.py remains the single results parser. This script only
decides which sessions are due, discovers missing Motorsport.com URLs, and calls
the existing fetcher sequentially.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.motorsport.com"
DEFAULT_TIMEZONE = "Europe/Amsterdam"
STATE_PATH = Path(".raceday/auto-results-state.json")
INITIAL_SEARCH_HOURS = 24
FETCH_EARLY_MINUTES = 30
FOLLOW_UP_MINUTES = (60, 120, 180, 240, 300, 360)
MAX_FETCHES_PER_RUN = 8

# RaceDay series id -> Motorsport.com URL segment. Unsupported/offical-source
# series are deliberately omitted so they are never guessed or fetched.
MOTORSPORT_SERIES = {
    "f1": "f1",
    "f2": "fia-f2",
    "f3": "fia-f3",
    "f1academy": "f1-academy",
    "wec": "wec",
    "imsa": "imsa",
    "indycar": "indycar",
    "motogp": "motogp",
    "moto2": "moto2",
    "moto3": "moto3",
    "nascar": "nascar-cup",
    "nascar_oreilly": "nascar-os",
    "nascar_trucks": "nascar-truck",
    "formulae": "formula-e",
    "wrc": "wrc",
    "dtm": "dtm",
    "supercars": "v8supercars",
    "elms": "elms",
    "wsbk": "wsbk",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class EventCandidate:
    label: str
    alias: str
    value: str
    url: str | None


@dataclass(frozen=True)
class SessionCandidate:
    label: str
    code: str
    url: str


def normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def compact(value: Any) -> str:
    return normalize(value).replace(" ", "")


def normalize_event(value: Any) -> str:
    text = normalize(value)
    match = re.fullmatch(r"grand prix of (.+)", text)
    if match:
        text = f"{match.group(1)} gp"
    else:
        match = re.fullmatch(r"(.+) grand prix", text)
        if match:
            text = f"{match.group(1)} gp"
    replacements = {
        "great britain gp": "british gp",
        "spain gp": "spanish gp",
        "france gp": "french gp",
        "italy gp": "italian gp",
        "hungary gp": "hungarian gp",
        "czechia gp": "czech gp",
        "portugal gp": "portuguese gp",
        "catalonia gp": "catalan gp",
        "the americas gp": "americas gp",
    }
    return replacements.get(text, text)


def event_terms(round_data: dict[str, Any]) -> set[str]:
    values = {
        normalize_event(round_data.get("raceName")),
        normalize(round_data.get("circuitName")),
        normalize(round_data.get("city")),
        normalize(round_data.get("country")),
    }
    circuit_words = [
        word for word in normalize(round_data.get("circuitName")).split()
        if word not in {"the", "of", "de", "la", "le", "circuit", "autodromo", "international", "speedway"}
    ]
    if len(circuit_words) >= 2:
        values.add("".join(word[0] for word in circuit_words))
    aliases = {
        "autodromo jose carlos pace": "interlagos",
        "circuit of the americas": "cota",
        "circuit de la sarthe": "le mans",
        "lusail international circuit": "losail",
    }
    round_aliases = {
        "rolex 24 at daytona": "rolex 24 hours",
        "chevrolet grand prix": "mosport",
        "autotrader 400": "atlanta",
        "quaker state 400": "atlanta ii",
        "coke zero sugar 400": "daytona ii",
        "enjoy illinois 300": "gateway",
        "nu way 225": "gateway",
        "nascar all star race": "north wilkesboro",
        "airtouch 500 at the bend": "the bend enduro",
    }
    circuit = normalize(round_data.get("circuitName"))
    if circuit in aliases:
        values.add(aliases[circuit])
    race_name = normalize(round_data.get("raceName"))
    if race_name in round_aliases:
        values.add(round_aliases[race_name])
    return {value for value in values if value}


def get_html(url: str, session: requests.Session | None = None) -> tuple[str, str]:
    client = session or requests.Session()
    response = client.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response.text, response.url


def extract_event_candidates(html: str, page_url: str) -> list[EventCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[EventCandidate] = []
    seen: set[tuple[str, str]] = set()

    for node in soup.select("[data-event-alias]"):
        alias = normalize(node.get("data-event-alias"))
        value = str(node.get("value") or node.get("data-value") or "")
        link = node if node.name == "a" else node.find("a", href=True)
        if link is None:
            link = node.find_parent("a", href=True)
        href = link.get("href") if link else None
        label = node.get_text(" ", strip=True) or alias
        url = urljoin(page_url, href) if href else None
        page_path = urlparse(page_url).path
        current_match = re.search(r"/results/20\d{2}/([^/?]+?)(?:-\d+)?/?$", page_path)
        current_slug = normalize(current_match.group(1).replace("-", " ")) if current_match else ""
        if url and not re.search(r"/results/20\d{2}/[^/?]+/?$", urlparse(url).path):
            url = None
        if not url and current_slug and (
            compact(alias) == compact(current_slug)
            or compact(label) == compact(current_slug)
        ):
            url = page_url
        key = (value or alias, url or "")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(EventCandidate(label, alias, value, url))

    if candidates:
        return candidates

    year_match = re.search(r"/results/(20\d{2})/", page_url)
    year = year_match.group(1) if year_match else r"20\d{2}"
    pattern = re.compile(rf"/results/{year}/[^/?#]+-\d+/?$")
    for link in soup.find_all("a", href=True):
        url = urljoin(page_url, link["href"])
        if not pattern.search(urlparse(url).path):
            continue
        label = link.get_text(" ", strip=True)
        key = (normalize(label), url)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(EventCandidate(label, "", "", url))
    return candidates


def event_score(candidate: EventCandidate, round_data: dict[str, Any]) -> float:
    candidate_values = [normalize_event(candidate.label), normalize_event(candidate.alias)]
    terms = event_terms(round_data)
    score = 0.0
    for candidate_value in candidate_values:
        if not candidate_value:
            continue
        for term in terms:
            if candidate_value == term or compact(candidate_value) == compact(term):
                score = max(score, 1.0)
            elif candidate_value in term or term in candidate_value:
                score = max(score, 0.82)
            else:
                score = max(score, SequenceMatcher(None, candidate_value, term).ratio() * 0.72)
    return score


def choose_event(candidates: list[EventCandidate], round_data: dict[str, Any]) -> EventCandidate | None:
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda item: event_score(item, round_data), reverse=True)
    best = ranked[0]
    if event_score(best, round_data) >= 0.52:
        return best

    return None


def event_alias_from_url(url: str) -> str:
    match = re.search(
        r"/results/20\d{2}/([^/?]+?)(?:-\d+)?/?$",
        urlparse(url).path,
    )
    return normalize(match.group(1).replace("-", " ")) if match else ""


def resolve_event_url(
    candidate: EventCandidate,
    season_url: str,
    session: requests.Session | None = None,
) -> str | None:
    """Resolve a disabled Motorsport.com event selector without guessing its id.

    Motorsport.com can publish an event page before adding a link to the season
    selector. The selector's numeric value is already usable through the site's
    own ``?event=`` route, which redirects to the canonical results URL. Accept
    that redirect only when its slug matches the selected event.
    """
    if candidate.url:
        return candidate.url
    if not candidate.value:
        return None

    separator = "&" if "?" in season_url else "?"
    lookup_url = f"{season_url}{separator}{urlencode({'event': candidate.value})}"
    _html, final_url = get_html(lookup_url, session)
    resolved_alias = event_alias_from_url(final_url)
    expected_aliases = {
        compact(candidate.alias),
        compact(candidate.label),
    }
    if resolved_alias and compact(resolved_alias) in expected_aliases:
        return final_url
    return None


def extract_session_candidates(html: str, event_url: str) -> list[SessionCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[SessionCandidate] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        url = urljoin(event_url, link["href"])
        query = parse_qs(urlparse(url).query)
        code = (query.get("st") or [""])[0].strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        label = link.get_text(" ", strip=True) or code
        candidates.append(SessionCandidate(label, code, url))
    return candidates


def number_in(value: str) -> int | None:
    match = re.search(r"(?:^|\D)(\d+)(?:\D|$)", value)
    return int(match.group(1)) if match else None


def session_score(candidate: SessionCandidate, session_data: dict[str, Any]) -> float:
    name = normalize(session_data.get("name"))
    kind = normalize(session_data.get("kind"))
    label = normalize(candidate.label)
    code = compact(candidate.code)
    score = SequenceMatcher(None, name, label).ratio() * 0.55 if name and label else 0.0
    wanted_number = number_in(name)
    code_number = number_in(code)

    if "practice" in name or kind == "practice":
        if code.startswith(("fp", "p", "pr")):
            score += 0.55
            if wanted_number and code_number == wanted_number:
                score += 0.45
    elif "sprint qualifying" in name or "sprintqualifying" in kind:
        if code.startswith(("sq", "qs")) or "sprint" in label and "qual" in label:
            score += 1.0
    elif "sprint" in name or "sprintrace" in kind:
        if code in {"SR", "SPRINT", "RACESPRINT"} or "sprint" in label:
            score += 1.0
    elif "qual" in name or "qual" in kind or "hyperpole" in name:
        if code.startswith(("q", "hq")) or "qual" in label or "hyperpole" in label:
            score += 0.8
            if wanted_number and code_number == wanted_number:
                score += 0.25
    elif "race" in name or kind in {"race", "feature race", "feature_race"}:
        if code in {"R", "RACE", "FR", "FEATURE"} or label == "race" or "race" in label:
            score += 1.0
    elif "warm" in name:
        if code.startswith("WU") or "warm" in label:
            score += 1.0
    return score


def supercars_session_role(session_data: dict[str, Any]) -> str | None:
    """Classify Supercars sessions without trusting their sometimes incorrect kind."""
    name = normalize(session_data.get("name"))
    kind = normalize(session_data.get("kind"))
    if "shootout" in name or "ttso" in name:
        return "shootout"
    if "qual" in name:
        return "qualifying"
    if "practice" in name or kind == "practice":
        return "practice"
    if "race" in name or kind in {"race", "feature race", "feature_race"}:
        return "race"
    return None


def session_for_matching(series: str, sessions: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Add an event-local ordinal used by series whose public names use season numbers."""
    session_data = dict(sessions[index])
    if series != "supercars":
        return session_data
    role = supercars_session_role(session_data)
    if not role:
        return session_data
    session_data["_eventOrdinal"] = sum(
        1 for previous in sessions[:index + 1]
        if supercars_session_role(previous) == role
    )
    session_data["_eventRole"] = role
    return session_data


def preferred_session_codes(series: str, session_data: dict[str, Any]) -> list[str] | None:
    """Return strict, series-aware result tabs in preference order.

    None means the generic matcher may be used. An empty list means that
    Motorsport.com has no safe single table for this RaceDay session.
    """
    name = normalize(session_data.get("name"))
    kind = normalize(session_data.get("kind"))
    number = number_in(name)
    is_practice = "practice" in name or kind == "practice"
    is_qualifying = "qual" in name or "qual" in kind or "hyperpole" in name or "superpole" in name
    is_race = "race" in name or kind in {"race", "feature race", "feature_race"}

    if series == "f1":
        if "sprint qualifying" in name or "sprintqualifying" in kind:
            return ["CSQ"]
        if "sprint" in name or "sprintrace" in kind:
            return ["SPR"]
        if is_qualifying:
            return ["CQ"]
        if is_practice:
            return [f"FP{number}"] if number else ["FP1", "FP"]
        if is_race:
            return ["RACE"]

    if series in {"f2", "f3"}:
        if "sprint" in name:
            return ["RACE1"]
        if is_qualifying:
            return ["Q"]
        if is_practice:
            return ["FP"]
        if "feature" in name or is_race:
            return ["RACE2"]

    if series == "f1academy":
        if is_practice:
            return ["FIP", "FP1", "FP"]
        if is_qualifying:
            return ["Q"]
        if is_race:
            if number:
                return [f"RACE{number}"]
            if "opening" in name or "reverse" in name:
                return ["RACE1"]
            if "feature" in name:
                return ["RACE2"]
            return ["RACE1", "RACE2"]

    if series == "wec":
        if "warm" in name:
            return ["W", "WU"]
        if is_practice:
            return [f"FP{number}"] if number else ["FP1"]
        if "hyperpole 2" in name and "lmp2" in name:
            return ["Q2"]
        if "hyperpole 1" in name and "hypercar" in name:
            return ["Q3"]
        if "hyperpole 2" in name and "hypercar" in name:
            return ["Q4"]
        if is_qualifying:
            return ["Q4", "Q3", "Q2", "Q1", "Q"]
        if is_race:
            return ["RACE"]

    if series == "imsa":
        if is_practice:
            return [f"FP{number}"] if number else ["FP1", "FIP"]
        if is_qualifying:
            return ["Q"]
        if is_race:
            return ["RACE"]

    if series == "indycar":
        if "warm" in name or "final practice" in name:
            return ["FIP", "FP2"]
        if is_qualifying:
            return ["Q", "CQ", "Q2", "Q1"]
        if is_practice:
            return [f"FP{number}"] if number else ["FP1", "FIP"]
        if is_race:
            return ["RACE"]

    if series in {"motogp", "moto2", "moto3"}:
        if "sprint" in name:
            return ["SPR"]
        if "warm" in name:
            return ["W"]
        if "free practice 1" in name:
            return ["FP1"]
        if name == "practice":
            return ["FIP"]
        if "free practice 2" in name:
            return ["FP2"]
        if is_qualifying:
            return [f"Q{number}"] if number else ["Q2", "Q"]
        if is_race:
            return ["RACE"]

    if series in {"nascar", "nascar_oreilly", "nascar_trucks"}:
        if is_qualifying:
            return ["Q", "Q2", "Q1"]
        if is_practice:
            return ["FIP", "FP1", "FP"]
        if is_race:
            return ["RACE"]

    if series == "formulae":
        if is_practice:
            return []  # Current Motorsport.com event pages do not expose practice tables.
        if is_qualifying:
            return []  # Knockout tabs are separate; no safe full qualifying table exists.
        if is_race:
            return ["RACE"]

    if series == "wrc":
        if "shakedown" in name or kind == "shakedown":
            return ["SHD"]
        if kind == "stage" and number:
            return [f"SS {number}", f"SS{number}"]
        if is_race:
            return ["RACE"]
        if kind == "stage":
            return []

    if series == "supercars":
        role = session_data.get("_eventRole") or supercars_session_role(session_data)
        ordinal = session_data.get("_eventOrdinal")
        if role == "practice":
            session_number = ordinal or number or 1
            return [f"FP{session_number}", "FIP", "FP"]
        if role == "qualifying":
            session_number = ordinal or number or 1
            return [f"Q{session_number}", "Q"]
        if role == "shootout":
            session_number = ordinal or 1
            return [f"SO{session_number}"]
        if role == "race":
            session_number = ordinal or number or 1
            return [f"RACE{session_number}", "RACE"]

    if series == "dtm":
        if is_practice:
            return [f"FP{number}"] if number else ["FP1", "FIP"]
        if is_qualifying:
            return [f"Q{number}"] if number else ["Q", "Q1"]
        if is_race:
            return [f"RACE{number}"] if number else ["RACE", "RACE1"]

    if series == "elms":
        if is_practice:
            return [f"FP{number}"] if number else ["FP1"]
        if is_qualifying:
            return ["Q4", "Q3", "Q2", "Q1", "Q"]
        if is_race:
            return ["RACE"]

    if series == "wsbk":
        if "superpole race" in name:
            return ["SUPERPOLE RACE"]
        if "superpole" in name or is_qualifying:
            return ["SP1"]
        if "warm" in name:
            return ["W"]
        if is_practice:
            return [f"FP{number}"] if number else ["FP1"]
        if is_race:
            return [f"RACE{number}"] if number else ["RACE1"]
    return None


def choose_session(
    candidates: list[SessionCandidate],
    session_data: dict[str, Any],
    series: str = "",
) -> SessionCandidate | None:
    if not candidates:
        return None
    preferred = preferred_session_codes(series, session_data)
    if preferred is not None:
        by_code = {item.code.upper(): item for item in candidates}
        for code in preferred:
            if code.upper() in by_code:
                return by_code[code.upper()]
        return None
    best = max(candidates, key=lambda item: session_score(item, session_data))
    return best if session_score(best, session_data) >= 0.62 else None


def first_fetch_at(session_data: dict[str, Any], timezone: ZoneInfo) -> datetime | None:
    date = session_data.get("date")
    time = session_data.get("timeLocal")
    if not date or not time:
        return None
    try:
        start = datetime.fromisoformat(f"{date}T{str(time)[:5]}").replace(tzinfo=timezone)
        duration = max(1, int(session_data.get("durationMinutes") or 60))
    except (TypeError, ValueError):
        return None
    return start + timedelta(minutes=max(0, duration - FETCH_EARLY_MINUTES))


def result_hash(results: list[dict[str, Any]]) -> str:
    payload = json.dumps(results, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sessions": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state.setdefault("version", 1)
        state.setdefault("sessions", {})
        return state
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "sessions": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        return None


def due_stage(entry: dict[str, Any], now: datetime) -> str | None:
    found_at = parse_iso(entry.get("foundAt"))
    if not found_at:
        return "initial"
    completed = set(entry.get("completedChecks") or [])
    elapsed = (now.astimezone(ZoneInfo("UTC")) - found_at.astimezone(ZoneInfo("UTC"))).total_seconds() / 60
    # Do not resurrect old sessions if scheduled Actions were disabled for a while.
    if elapsed > FOLLOW_UP_MINUTES[-1] + 30:
        return None
    for minutes in FOLLOW_UP_MINUTES:
        stage = f"{minutes}m"
        if elapsed >= minutes and stage not in completed:
            return stage
    return None


def find_session(rounds: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    for round_data in rounds:
        for session_data in round_data.get("sessions", []):
            if session_data.get("id") == session_id:
                return session_data
    return None


def call_existing_fetcher(url: str, session_id: str, series: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"🧪 Would fetch {series}/{session_id}: {url}")
        return True
    command = [
        sys.executable,
        "run_fetch_results.py",
        "--url", url,
        "--session-id", session_id,
        "--series", series,
    ]
    result = subprocess.run(command, check=False)
    return result.returncode == 0


def json_files(year: int) -> list[Path]:
    return sorted(
        path for path in Path(".").glob(f"*_{year}.json")
        if not path.name.endswith(f"_standings_{year}.json")
    )


def run(args: argparse.Namespace) -> int:
    timezone = ZoneInfo(args.timezone)
    now = datetime.fromisoformat(args.now).astimezone(timezone) if args.now else datetime.now(timezone)
    state = load_state(Path(args.state))
    session_state = state["sessions"]
    http = requests.Session()
    season_cache: dict[str, tuple[list[EventCandidate], str]] = {}
    event_cache: dict[str, tuple[str, list[SessionCandidate]]] = {}
    event_resolution_cache: dict[tuple[str, str], str | None] = {}
    fetch_count = 0
    state_changed = False

    for path in json_files(args.year):
        series = path.name.removesuffix(f"_{args.year}.json")
        if args.series and series != args.series:
            continue
        motorsport_series = MOTORSPORT_SERIES.get(series)
        if not motorsport_series:
            continue
        try:
            rounds = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"⚠️ Skipping {path}: {error}")
            continue
        if not isinstance(rounds, list):
            continue

        for round_data in rounds:
            if args.round_id and round_data.get("id") != args.round_id:
                continue
            round_sessions = round_data.get("sessions", [])
            for session_index, session_data in enumerate(round_sessions):
                session_id = session_data.get("id")
                first_fetch = first_fetch_at(session_data, timezone)
                force_event = bool(args.force_event and args.series and args.round_id)
                if not session_id:
                    continue
                if not force_event and (not first_fetch or now < first_fetch):
                    continue

                entry = session_state.get(session_id)
                existing_results = session_data.get("results") or []

                if not force_event and entry is None and existing_results:
                    # Adopt a recent manually fetched result so it receives the
                    # same precision refreshes without touching the manual path.
                    if now <= first_fetch + timedelta(hours=6):
                        session_state[session_id] = {
                            "foundAt": now.astimezone(ZoneInfo("UTC")).isoformat(),
                            "completedChecks": [],
                            "lastResultHash": result_hash(existing_results),
                            "sourceUrl": session_data.get("resultsUrl") or None,
                        }
                        state_changed = True
                    continue

                if not force_event and entry is None and now > first_fetch + timedelta(hours=INITIAL_SEARCH_HOURS):
                    continue

                entry = entry or {}
                stage = "event" if force_event else due_stage(entry, now)
                if not stage:
                    continue
                if fetch_count >= args.max_fetches:
                    print(f"ℹ️ Fetch limit ({args.max_fetches}) reached; remaining sessions wait for next run")
                    if state_changed and not args.dry_run:
                        save_state(Path(args.state), state)
                    return 0

                source_url = (session_data.get("resultsUrl") or entry.get("sourceUrl") or "").strip()
                if not source_url:
                    if series not in season_cache:
                        season_url = f"{BASE_URL}/{motorsport_series}/results/{args.year}/"
                        try:
                            season_html, final_url = get_html(season_url, http)
                            season_cache[series] = (extract_event_candidates(season_html, final_url), final_url)
                        except requests.RequestException as error:
                            print(f"⚠️ {series}: season page unavailable: {error}")
                            season_cache[series] = ([], season_url)
                    candidate = choose_event(season_cache[series][0], round_data)
                    if not candidate:
                        print(f"⏳ {series}/{round_data.get('raceName')}: event results link not published yet")
                        continue
                    event_url = candidate.url
                    if not event_url and candidate.value:
                        resolution_key = (series, candidate.value)
                        if resolution_key not in event_resolution_cache:
                            season_url = f"{BASE_URL}/{motorsport_series}/results/{args.year}/"
                            try:
                                event_resolution_cache[resolution_key] = resolve_event_url(
                                    candidate,
                                    season_url,
                                    http,
                                )
                            except requests.RequestException as error:
                                print(f"⚠️ {series}/{candidate.label}: event lookup unavailable: {error}")
                                event_resolution_cache[resolution_key] = None
                        event_url = event_resolution_cache[resolution_key]
                    if not event_url:
                        print(f"⏳ {series}/{round_data.get('raceName')}: event results link not published yet")
                        continue
                    if event_url not in event_cache:
                        try:
                            event_html, final_event_url = get_html(event_url, http)
                            event_cache[event_url] = (
                                final_event_url,
                                extract_session_candidates(event_html, final_event_url),
                            )
                        except requests.RequestException as error:
                            print(f"⚠️ {series}/{candidate.label}: event page unavailable: {error}")
                            continue
                    matching_session = session_for_matching(series, round_sessions, session_index)
                    session_candidate = choose_session(event_cache[event_url][1], matching_session, series)
                    if not session_candidate:
                        print(f"⏳ {series}/{session_data.get('name')}: session results link not published yet")
                        continue
                    source_url = session_candidate.url

                print(f"🏁 {stage}: {series}/{session_data.get('name')} -> {source_url}")
                if not call_existing_fetcher(source_url, session_id, series, args.dry_run):
                    print(f"⚠️ Fetch failed; it will retry on the next scheduled run")
                    continue
                fetch_count += 1
                if args.dry_run:
                    continue

                # fetch_results.py rewrites the file, so reload before recording state.
                rounds = json.loads(path.read_text(encoding="utf-8"))
                updated_session = find_session(rounds, session_id)
                updated_results = (updated_session or {}).get("results") or []
                if not updated_results:
                    print("⚠️ Fetcher returned without results; not marking the check complete")
                    continue

                checked_at = now.astimezone(ZoneInfo("UTC")).isoformat()
                previous_hash = entry.get("lastResultHash")
                new_hash = result_hash(updated_results)
                if stage == "event":
                    print(f"✅ Event import completed for {session_id}")
                    continue
                if stage == "initial":
                    entry["foundAt"] = checked_at
                    entry.setdefault("completedChecks", [])
                else:
                    entry.setdefault("completedChecks", []).append(stage)
                    entry["completedChecks"] = list(dict.fromkeys(entry["completedChecks"]))
                entry.update({
                    "lastCheckedAt": checked_at,
                    "lastResultHash": new_hash,
                    "sourceUrl": source_url,
                })
                session_state[session_id] = entry
                state_changed = True
                print("✅ Results updated" if previous_hash != new_hash else "✅ Checked; results unchanged")

    if state_changed and not args.dry_run:
        save_state(Path(args.state), state)
    print(f"ℹ️ Completed with {fetch_count} result fetch(es)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--timezone", default=os.environ.get("RACEDAY_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--max-fetches", type=int, default=MAX_FETCHES_PER_RUN)
    parser.add_argument("--series", help="Only process this RaceDay series id")
    parser.add_argument("--round-id", help="Only process this RaceDay event id")
    parser.add_argument(
        "--force-event",
        action="store_true",
        help="Fetch every discoverable session in the selected event, regardless of date/results state",
    )
    parser.add_argument("--now", help="ISO timestamp, used for tests/manual dry-runs")
    parser.add_argument("--dry-run", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
