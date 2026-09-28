#!/usr/bin/env python3
"""
RaceDay Standings Fetcher
Fetches driver/team standings from a Motorsport.com standings page
and saves to {series}_standings_2026.json.

Usage:
  python fetch_standings.py \
    --url "https://www.motorsport.com/f1/standings/2026/" \
    --series "f1"
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]


def get_html(url: str, retries: int = 3) -> str:
    import hashlib
    idx = int(hashlib.md5(url.encode()).hexdigest(), 16) % len(USER_AGENTS)
    headers = {
        "User-Agent": USER_AGENTS[idx],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.motorsport.com/",
    }
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise e


def clean_name(s: str) -> str:
    """Shorten 'Lando Norris' → 'L. Norris' style (as displayed in app)."""
    s = s.strip()
    # If already abbreviated, return as-is
    parts = s.split()
    if len(parts) >= 2 and len(parts[0]) <= 2 and parts[0].endswith('.'):
        return s
    if len(parts) >= 2:
        return f"{parts[0][0]}. {' '.join(parts[1:])}"
    return s


def parse_standings(html: str, kind: str = "drivers") -> list[dict]:
    """Parse a motorsport.com standings page HTML into a list of dicts."""
    soup = BeautifulSoup(html, "html.parser")

    # Motorsport.com renders tables in <table> elements with class containing 'ms-table'
    # or sometimes uses divs. Try tables first.
    entries = []

    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Detect header row
        header_cells = rows[0].find_all(["th", "td"])
        header_texts = [c.get_text(strip=True).upper() for c in header_cells]

        pos_col    = next((i for i, h in enumerate(header_texts) if "POS" in h or "#" == h), None)
        driver_col = next((i for i, h in enumerate(header_texts) if "DRIVER" in h or "RIDER" in h or "PILOT" in h), None)
        team_col   = next((i for i, h in enumerate(header_texts) if "TEAM" in h or "CONSTRUCTOR" in h), None)
        pts_col    = next((i for i, h in enumerate(header_texts) if "PTS" in h or "POINTS" in h), None)

        if kind == "teams":
            # Never interpret a driver table as a team championship.
            if driver_col is not None:
                continue
            driver_col = team_col

        if driver_col is None or pts_col is None:
            continue  # not a standings table

        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) <= max(i for i in [pos_col, driver_col, team_col, pts_col] if i is not None):
                continue

            # Position
            pos_text = cols[pos_col].get_text(strip=True) if pos_col is not None else ""
            try:
                pos = int(re.sub(r"[^\d]", "", pos_text))
            except ValueError:
                continue  # skip header/separator rows

            # Driver name — only the driver link text, NOT the team sub-text
            driver_cell = cols[driver_col]

            # Motorsport.com often nests team name inside the driver cell as a
            # second link or span. Extract only the first <a> that points to a
            # driver/rider/pilot profile URL, otherwise fall back to first link.
            driver_links = driver_cell.find_all("a")
            driver_link = next(
                (l for l in driver_links if any(kw in (l.get("href") or "") for kw in ["/driver", "/rider", "/pilot"])),
                driver_links[0] if driver_links else None
            )
            if driver_link:
                # Take only the first direct text node inside the link —
                # motorsport.com nests the team name as a child span inside
                # the same <a>, so get_text() would return "AntonelliMercedes".
                first_text = next(
                    (s.strip() for s in driver_link.strings if s.strip()), ""
                )
                name = first_text if first_text else driver_link.get_text(strip=True)
            else:
                # No links — grab only the first direct text node of the cell
                name = next(
                    (s.strip() for s in driver_cell.strings if s.strip()), ""
                )
            name = clean_name(name) if kind == "drivers" else name
            if not name:
                continue

            # Team — prefer explicit team column; otherwise look for a second
            # link or a sub-span inside the driver cell (motorsport.com style).
            team = ""
            if team_col is not None:
                team_cell = cols[team_col]
                team_links = team_cell.find_all("a")
                team = (team_links[0].get_text(strip=True) if team_links
                        else team_cell.get_text(strip=True)).strip()
            else:
                # Team embedded in driver cell as second link or a span with 'team' in class
                team_link = next(
                    (l for l in driver_links if l is not driver_link),
                    None
                )
                if team_link:
                    team = team_link.get_text(strip=True)
                else:
                    team_span = driver_cell.find(
                        lambda tag: tag.name in ("span", "div", "p", "small")
                        and any("team" in (c or "").lower() for c in tag.get("class", []))
                    )
                    if team_span:
                        team = team_span.get_text(strip=True)

            # Points
            pts_text = cols[pts_col].get_text(strip=True)
            pts_text = re.sub(r"[^\d.\-]", "", pts_text)
            try:
                pts = float(pts_text) if pts_text else 0
                pts = int(pts) if float(pts).is_integer() else pts
            except ValueError:
                pts = 0

            entries.append({"position": pos, "name": name, "team": team, "points": pts})

    if entries:
        entries.sort(key=lambda x: x["position"])
        return entries

    return []


MOTORSPORT_SERIES = {
    "f1": "f1", "f2": "fia-f2", "f3": "fia-f3", "f1academy": "f1-academy",
    "formulae": "formula-e", "motogp": "motogp", "moto2": "moto2", "moto3": "moto3",
    "wsbk": "wsbk", "indycar": "indycar", "indynxt": "indylights", "sf": "super-formula",
    "nascar": "nascar-cup", "nascar_oreilly": "nascar-os", "nascar_trucks": "nascar-truck",
    "wec": "wec", "imsa": "imsa", "elms": "elms", "dtm": "dtm", "wrc": "wrc",
    "supercars": "v8supercars", "btcc": "btcc", "british_gt": "british-gt",
}


def standings_url(url, kind):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ("www.motorsport.com", "motorsport.com"):
        raise ValueError("Use an HTTPS Motorsport.com standings URL")
    if not re.fullmatch(r"/[^/]+/standings/\d{4}/?", parts.path):
        raise ValueError("URL must identify a series and standings year")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "type"]
    query.append(("type", "Team" if kind == "teams" else "Driver"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def has_team_standings(html):
    soup = BeautifulSoup(html, "html.parser")
    return any(dict(parse_qsl(urlsplit(tag.get("href", "")).query)).get("type") == "Team"
               for tag in soup.find_all("a", href=True)) or any(
        option.get("value") == "Team" for option in soup.find_all("option"))


def fetch_series(url, series, round_id="", round_name=""):
    if not re.fullmatch(r"[a-z0-9_]+", series):
        raise ValueError("Invalid series ID")
    driver_url = standings_url(url, "drivers")
    year = re.search(r"/standings/(\d{4})", driver_url).group(1)
    out = Path(f"{series}_standings_{year}.json")
    old = json.loads(out.read_text()) if out.exists() else {}
    if isinstance(old, list):
        old = {"drivers": old}
    html = get_html(driver_url)
    drivers = parse_standings(html)
    if not drivers:
        raise ValueError(f"{series}: no driver standings; existing file preserved")
    payload = dict(old)
    payload.update(drivers=drivers)
    team_error = None
    explicit_team = dict(parse_qsl(urlsplit(url).query)).get("type") == "Team"
    if has_team_standings(html) or explicit_team:
        try:
            teams = parse_standings(get_html(standings_url(url, "teams")), "teams")
            if not teams:
                raise ValueError("No team standings found; existing teams preserved")
            payload["teams"] = teams
            payload["teamsUpdatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception as exc:
            team_error = exc
    else:
        print(f"{series}: no Teams championship advertised; existing teams preserved")
    payload.update(
        updatedAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        updatedAfterRoundId=round_id or old.get("updatedAfterRoundId"),
        updatedAfterRoundName=round_name or old.get("updatedAfterRoundName"),
    )
    # Avoid a new commit every time unchanged standings are checked.
    comparable = lambda value: {k: v for k, v in value.items() if k not in ("updatedAt", "teamsUpdatedAt")}
    if comparable(payload) != comparable(old):
        temporary = out.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(out)
    print(f"{series}: {len(drivers)} drivers, {len(payload.get('teams', []))} teams")
    if team_error:
        raise ValueError(f"{series}: {team_error}")


def main():
    parser = argparse.ArgumentParser(description="Fetch driver and team standings from Motorsport.com")
    parser.add_argument("--url")
    parser.add_argument("--series")
    parser.add_argument("--all", action="store_true", help="Fetch series advertised on the all-standings page")
    parser.add_argument("--year", type=int, default=datetime.now(timezone.utc).year)
    parser.add_argument("--updated-after-round-id", default="")
    parser.add_argument("--updated-after-round-name", default="")
    args = parser.parse_args()
    if args.all:
        index_url = f"https://www.motorsport.com/all/standings/{args.year}/"
        soup = BeautifulSoup(get_html(index_url), "html.parser")
        available = {urlsplit(urljoin(index_url, a["href"])).path.rstrip("/")
                     for a in soup.find_all("a", href=True)}
        jobs = [(series, f"https://www.motorsport.com/{slug}/standings/{args.year}/")
                for series, slug in MOTORSPORT_SERIES.items()
                if f"/{slug}/standings/{args.year}" in available or f"/{slug}/standings" in available]
        if not jobs:
            parser.error("No supported standings links found on the index; files preserved")
    elif args.url and args.series:
        jobs = [(args.series, args.url)]
    else:
        parser.error("Supply --all or both --url and --series")
    failures = []
    for series, url in jobs:
        try:
            fetch_series(url, series, args.updated_after_round_id, args.updated_after_round_name)
        except Exception as exc:
            failures.append(str(exc))
            print(f"WARNING: {series}: {exc}", file=sys.stderr)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
