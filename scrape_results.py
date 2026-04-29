#!/usr/bin/env python3
"""
RaceDay Results Scraper — NASCAR Cup
Scrapes race results from Motorsport.com and updates the nascar_2026.json file.

Usage:
  python scrape_results.py --series nascar --session-id nascar-2026-r10-s3
  python scrape_results.py --series nascar --all-finished
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Series config ────────────────────────────────────────────────────────────

SERIES_CONFIG = {
    "nascar":     {"ms_slug": "nascar-cup",  "json": "nascar_2026.json"},
    "nascar_oreilly": {"ms_slug": "nascar-os", "json": "nascar_oreilly_2026.json"},
    "nascar_trucks":  {"ms_slug": "nascar-truck", "json": "nascar_trucks_2026.json"},
    "indycar":    {"ms_slug": "indycar",     "json": "indycar_2026.json"},
    "wec":        {"ms_slug": "wec",         "json": "wec_2026.json"},
    "imsa":       {"ms_slug": "imsa",        "json": "imsa_2026.json"},
    "f1":         {"ms_slug": "f1",          "json": "f1_2026.json"},
    "f2":         {"ms_slug": "fia-f2",      "json": "f2_2026.json"},
    "f3":         {"ms_slug": "fia-f3",      "json": "f3_2026.json"},
    "motogp":     {"ms_slug": "motogp",      "json": "motogp_2026.json"},
    "moto2":      {"ms_slug": "moto2",       "json": "moto2_2026.json"},
    "moto3":      {"ms_slug": "moto3",       "json": "moto3_2026.json"},
    "formulae":   {"ms_slug": "formula-e",   "json": "formulae_2026.json"},
    "wrc":        {"ms_slug": "wrc",         "json": "wrc_2026.json"},
    "dtm":        {"ms_slug": "dtm",         "json": "dtm_2026.json"},
    "supercars":  {"ms_slug": "v8supercars", "json": "supercars_2026.json"},
    "elms":       {"ms_slug": "elms",        "json": "elms_2026.json"},
    "gtwce":      {"ms_slug": "gt-world-challenge-europe", "json": "gtwce_2026.json"},
    "british_gt": {"ms_slug": "british-gt",  "json": "british_gt_2026.json"},
}

SESSION_KIND_MAP = {
    "race":       "RACE",
    "qualifying": "EL",   # Motorsport.com uses EL for qualifying/entry list
    "practice":   "EL",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: list) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ Saved {path}")


def session_is_finished(session: dict) -> bool:
    """Returns True if session end time is in the past."""
    date_str = session.get("date")
    time_str = session.get("timeUTC") or session.get("timeLocal")
    duration = session.get("durationMinutes", 60)

    if not date_str or not time_str:
        return False

    try:
        dt_str = f"{date_str}T{time_str}:00+00:00"
        start = datetime.fromisoformat(dt_str)
        end = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        from datetime import timedelta
        end = end + timedelta(minutes=duration)
        return datetime.now(timezone.utc) > end
    except Exception:
        return False


def session_already_has_results(session: dict) -> bool:
    results = session.get("results", [])
    return len(results) >= 3


# ── Motorsport.com scraping ───────────────────────────────────────────────────

def get_all_event_slugs(ms_slug: str) -> list:
    """
    Get all event slugs with their date ranges from a results page sidebar.
    Returns list of (slug, date_range_text) tuples.
    """
    # Use a known working URL to get the full sidebar
    url = f"https://www.motorsport.com/{ms_slug}/results/2026/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️  Could not fetch results page: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    seen = set()

    # Find all links matching the results pattern
    links = soup.find_all("a", href=re.compile(rf"/{ms_slug}/results/2026/[^/?]+"))
    for link in links:
        href = link.get("href", "")
        slug_match = re.search(rf"/{ms_slug}/results/2026/([^/?]+)", href)
        if not slug_match:
            continue
        slug = slug_match.group(1)
        if slug in seen:
            continue
        seen.add(slug)

        # Get surrounding text including dates
        # Walk up to find the container with date info
        date_range = ""
        node = link.parent
        for _ in range(6):
            if node is None:
                break
            text = node.get_text(separator=" ", strip=True)
            # Look for date pattern like "Apr 23, 2026 to Apr 26, 2026"
            date_match = re.search(r'(\w{3})\s+(\d+),?\s+2026', text)
            if date_match:
                date_range = text[:150]
                break
            node = node.parent

        results.append((slug, date_range))

    return results


def get_event_slug(ms_slug: str, race_date: str, round_name: str) -> str | None:
    """
    Find the correct event slug by matching race date against event date ranges.
    """
    candidates = get_all_event_slugs(ms_slug)
    print(f"📋 Found {len(candidates)} events")

    if not candidates:
        return None

    # Parse race date
    try:
        race_dt = datetime.strptime(race_date, "%Y-%m-%d")
    except ValueError:
        race_dt = None

    month_names = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                   "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

    best_slug = None
    best_diff = 999

    for slug, date_range in candidates:
        if not date_range or not race_dt:
            continue

        # Extract all dates from date_range text
        date_matches = re.findall(r'(\w{3})\s+(\d+)', date_range)
        for month_str, day_str in date_matches:
            month_num = month_names.get(month_str.lower())
            if not month_num:
                continue
            try:
                event_dt = datetime(2026, month_num, int(day_str))
                diff = abs((race_dt - event_dt).days)
                if diff < best_diff:
                    best_diff = diff
                    best_slug = slug
            except ValueError:
                continue

    if best_slug and best_diff <= 7:
        print(f"📍 Matched by date: {best_slug} (diff: {best_diff} days)")
        return best_slug

    # Fallback: match by name against slug
    stop_words = {"the", "at", "of", "for", "and", "race", "grand", "prix",
                  "hours", "hour", "500", "400", "300", "200", "series", "cup",
                  "jack", "links", "wurth", "goodyear"}
    name_words = [w for w in re.split(r'\W+', round_name.lower())
                  if w and len(w) > 2 and w not in stop_words]

    best_score = 0
    for slug, _ in candidates:
        score = sum(1 for w in name_words if w in slug.lower())
        if score > best_score:
            best_score = score
            best_slug = slug

    if best_slug and best_score > 0:
        print(f"📍 Fallback name match: {best_slug} (score: {best_score})")
        return best_slug

    print(f"⚠️  No match for '{round_name}' on {race_date}")
    return None


def scrape_results(ms_slug: str, event_slug: str, session_kind: str) -> list:
    """
    Scrape results table from Motorsport.com.
    Table columns: Cla | Driver+Team | # | (flag) | Manufacturer | Laps | Time | Interval | Pits | Points | Retirement
    Returns list of result dicts.
    """
    st_param = "RACE" if session_kind == "race" else "EL"
    url = f"https://www.motorsport.com/{ms_slug}/results/2026/{event_slug}/?st={st_param}"

    print(f"🔍 Fetching: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️  Could not fetch results page: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if not table:
        print("⚠️  No results table found on page")
        return []

    # Read header to determine column indices
    header_row = table.find("tr")
    headers = [th.get_text(strip=True).upper() for th in header_row.find_all(["th", "td"])] if header_row else []
    print(f"📋 Columns: {headers}")

    rows = table.find_all("tr")[1:]
    results = []

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        try:
            # Col 0: Position
            position_text = cols[0].get_text(strip=True)
            position = int(position_text) if position_text.isdigit() else None

            # Col 1: Driver + Team (combined cell)
            driver_cell = cols[1]
            driver_links = driver_cell.find_all("a")
            cell_text = driver_cell.get_text(separator="|", strip=True)
            parts = [p.strip() for p in cell_text.split("|") if p.strip()]

            # Extract driver name and team separately
            # Format is typically: "Country  Abbreviated.Name  Team Name"
            driver_name = ""
            team_name = ""
            if len(parts) >= 2:
                # Last part that looks like a team (contains Racing, Motorsports, etc.)
                for i, part in enumerate(parts):
                    if any(w in part.lower() for w in ["racing", "motorsports", "motorsport",
                                                        "penske", "gibbs", "hendrick", "ferrari",
                                                        "porsche", "toyota", "ford", "chevrolet",
                                                        "spire", "trackhouse", "legacy", "front row",
                                                        "haas", "kaulig", "hyak", "23xi", "rfk", "jgr",
                                                        "wood brothers", "rick ware", "live fast"]):
                        team_name = part
                        # Driver is the part before team
                        driver_candidates = parts[:i]
                        if driver_candidates:
                            driver_name = driver_candidates[-1]
                        break

                if not driver_name and parts:
                    # Fallback: second part is usually the driver abbreviated name
                    driver_name = parts[1] if len(parts) > 1 else parts[0]
                    team_name = parts[-1] if len(parts) > 2 else ""

            # Col 2: Car number
            number = cols[2].get_text(strip=True) if len(cols) > 2 else ""

            # Col 4: Manufacturer (col 3 is usually a flag/image)
            manufacturer = cols[4].get_text(strip=True) if len(cols) > 4 else ""

            # Col 5: Laps
            laps = cols[5].get_text(strip=True) if len(cols) > 5 else ""

            # Col 6: Time
            time_val = cols[6].get_text(strip=True) if len(cols) > 6 else ""

            # Col 7: Interval
            interval = cols[7].get_text(strip=True) if len(cols) > 7 else ""
            # Clean up interval — remove the absolute time that's sometimes appended
            if interval and len(interval) > 15:
                # Keep only the gap part (e.g. "+1.752" from "+1.75257'21.526")
                interval = re.sub(r'(\+[\d.]+)[\d\'"]+.*', r'\1', interval)

            result = {
                "position": position,
                "driver": driver_name,
                "team": team_name,
                "number": number,
                "manufacturer": manufacturer,
                "laps": laps,
                "time": time_val,
                "interval": interval,
            }

            results.append(result)

        except Exception as e:
            print(f"⚠️  Row parse error: {e}")
            continue

    print(f"✅ Found {len(results)} results")
    return results


def extract_driver_name(cell_text: str) -> str:
    """Extract driver name from Motorsport.com cell text."""
    # Pattern: "Country  F. LastName  Team"
    # The abbreviated name appears after the country
    lines = [l.strip() for l in cell_text.split("  ") if l.strip()]
    for part in lines:
        # Driver names typically contain a dot (F. LastName) or are 2+ words
        if "." in part or (len(part.split()) >= 2 and len(part) < 40):
            # Exclude team names (usually longer or contain Racing/Motorsports)
            if not any(w in part.lower() for w in ["racing", "motorsports", "team", "penske", "gibbs"]):
                return part
    return lines[0] if lines else cell_text[:30]


def extract_team_name(cell_text: str) -> str:
    """Extract team name from Motorsport.com cell text."""
    lines = [l.strip() for l in cell_text.split("  ") if l.strip()]
    # Team name is usually the last meaningful part
    for part in reversed(lines):
        if any(w in part.lower() for w in ["racing", "motorsports", "team", "penske",
                                             "gibbs", "hendrick", "chevrolet", "ford",
                                             "toyota", "ferrari", "porsche"]):
            return part
    return lines[-1] if len(lines) > 1 else ""


# ── Main logic ────────────────────────────────────────────────────────────────

def process_series(series_id: str, target_session_id: str = None, all_finished: bool = False, force: bool = False):
    config = SERIES_CONFIG.get(series_id)
    if not config:
        print(f"❌ Unknown series: {series_id}")
        sys.exit(1)

    json_path = config["json"]
    ms_slug = config["ms_slug"]

    if not Path(json_path).exists():
        print(f"❌ JSON file not found: {json_path}")
        sys.exit(1)

    rounds = load_json(json_path)
    changed = False

    for round_data in rounds:
        for session in round_data.get("sessions", []):
            session_id = session.get("id")

            # Filter by specific session ID if provided
            if target_session_id and session_id != target_session_id:
                continue

            # Skip if not finished yet
            if not session_is_finished(session):
                if target_session_id:
                    print(f"⏳ Session {session_id} has not finished yet")
                continue

            # Skip if already has results (unless forced)
            if session_already_has_results(session) and not target_session_id and not force:
                continue

            print(f"\n🏁 Processing: {session_id} ({session.get('name')})")

            # Get event slug — gebruik race sessie datum
            race_sessions = round_data.get("sessions", [])
            race_date = next(
                (s.get("date", "") for s in race_sessions if s.get("kind") == "race"),
                race_sessions[0].get("date", "") if race_sessions else ""
            )

            event_slug = get_event_slug(
                ms_slug,
                race_date,
                round_data.get("raceName", "")
            )

            if not event_slug:
                print(f"⚠️  Could not find event slug for {round_data.get('raceName')}")
                continue

            # Scrape results
            time.sleep(1)  # Be polite
            results = scrape_results(ms_slug, event_slug, session.get("kind", "race"))

            if len(results) < 3:
                print(f"⚠️  Not enough results ({len(results)}), skipping")
                continue

            session["results"] = results
            changed = True
            print(f"✅ Updated {session_id} with {len(results)} results")

    if changed:
        save_json(json_path, rounds)
        print(f"\n🎉 Done! Updated {json_path}")
    else:
        print("\n✨ No updates needed")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RaceDay Results Scraper")
    parser.add_argument("--series", required=True, help="Series ID (e.g. nascar, wec, indycar)")
    parser.add_argument("--session-id", help="Specific session ID to update")
    parser.add_argument("--all-finished", action="store_true",
                        help="Update all finished sessions without results")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing results")
    args = parser.parse_args()

    process_series(
        series_id=args.series,
        target_session_id=args.session_id,
        all_finished=args.all_finished,
        force=args.force
    )
