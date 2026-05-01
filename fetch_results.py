#!/usr/bin/env python3
"""
RaceDay Results Fetcher
Fetches results from a known Motorsport.com URL and saves to the correct session in JSON.

Usage:
  python fetch_results.py \
    --url "https://www.motorsport.com/wec/results/2026/imola-665437/" \
    --session-id "wec-2026-r01-s5" \
    --series "wec"
"""

import argparse
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SERIES_JSON = {
    "f1":           "f1_2026.json",
    "f2":           "f2_2026.json",
    "f3":           "f3_2026.json",
    "wec":          "wec_2026.json",
    "imsa":         "imsa_2026.json",
    "indycar":      "indycar_2026.json",
    "motogp":       "motogp_2026.json",
    "moto2":        "moto2_2026.json",
    "moto3":        "moto3_2026.json",
    "nascar":       "nascar_2026.json",
    "nascar_oreilly": "nascar_oreilly_2026.json",
    "nascar_trucks":  "nascar_trucks_2026.json",
    "formulae":     "formulae_2026.json",
    "wrc":          "wrc_2026.json",
    "dtm":          "dtm_2026.json",
    "supercars":    "supercars_2026.json",
    "elms":         "elms_2026.json",
    "gtwce":        "gtwce_2026.json",
    "british_gt":   "british_gt_2026.json",
    "nls":          "nls_2026.json",
}

# Rotate through different user agents to avoid detection
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

def get_headers(url: str) -> dict:
    import hashlib, time
    # Pick user agent based on URL hash for consistency
    idx = int(hashlib.md5(url.encode()).hexdigest(), 16) % len(USER_AGENTS)
    return {
        "User-Agent": USER_AGENTS[idx],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def fetch_page(url: str) -> str:
    import time
    # Small random delay to appear more human
    time.sleep(1.5)
    resp = requests.get(url, headers=get_headers(url), timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_results(html: str, url: str = "") -> list:
    is_race = "st=race" in url.lower() or url.lower().endswith("/")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        raise ValueError("No results table found on page")

    # Read headers
    header_row = table.find("tr")
    headers = []
    if header_row:
        headers = [th.get_text(strip=True).upper()
                   for th in header_row.find_all(["th", "td"])]
    print(f"📋 Columns: {headers}")

    # Column index helpers
    def col_idx(names):
        for name in names:
            try:
                return headers.index(name)
            except ValueError:
                pass
        return -1

    # WEC has: CLA | TEAM | # | DRIVERS | CAR | LAPS | TIME | INTERVAL | PITS | RETIREMENT | POINTS
    # NASCAR:  CLA | DRIVER | # | (flag) | MANUFACTURER | LAPS | TIME | INTERVAL | PITS | POINTS | RETIREMENT
    team_col    = col_idx(["TEAM"])
    driver_col  = col_idx(["DRIVER", "DRIVERS"])
    number_col  = col_idx(["#"])
    laps_idx    = col_idx(["LAPS"])
    time_idx    = col_idx(["TIME"])
    int_idx     = col_idx(["INTERVAL", "GAP"])

    # If TEAM is col 1 and DRIVERS is col 3 → WEC style
    # If DRIVER is col 1 → single driver style (NASCAR, F1 etc)
    is_multi_driver = team_col == 1 and driver_col > 1

    print(f"📋 Multi-driver format: {is_multi_driver}, team_col={team_col}, driver_col={driver_col}")

    # Debug first 2 rows
    for i, row in enumerate(table.find_all("tr")[1:3]):
        dbg_cols = row.find_all("td")
        if len(dbg_cols) > 1:
            print(f"🔍 Row {i+1} col1: {str(dbg_cols[1])[:600]}")

    rows = table.find_all("tr")[1:]
    results = []
    team_keywords = [
        "racing", "motorsports", "motorsport", "penske", "gibbs", "hendrick",
        "ferrari", "porsche", "toyota", "bmw", "alpine", "cadillac", "af corse",
        "jota", "proton", "united", "prema", "hypercar", "team", "sport",
        "auto", "garage", "works", "factory", "official", "spire", "trackhouse",
        "legacy", "front row", "haas", "kaulig", "rfk", "23xi", "wood brothers",
    ]

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        try:
            # Position (col 0)
            pos_text = cols[0].get_text(strip=True)
            position = int(pos_text) if pos_text.isdigit() else None

            if is_multi_driver:
                # WEC style: col 1 = team, col 3 = drivers
                team_cell = cols[team_col] if team_col >= 0 and len(cols) > team_col else None
                driver_cell = cols[driver_col] if driver_col >= 0 and len(cols) > driver_col else None

                # Extract team name (span.name)
                team = ""
                if team_cell:
                    name_span = team_cell.find("span", class_="name")
                    team = name_span.get_text(strip=True) if name_span else team_cell.get_text(strip=True)

                # Extract drivers (multiple links or spans)
                drivers = []
                if driver_cell:
                    driver_links = driver_cell.find_all("a")
                    if driver_links:
                        drivers = [l.get_text(strip=True) for l in driver_links if l.get_text(strip=True)]
                    else:
                        # Plain text separated by / or newline
                        text = driver_cell.get_text(separator="/", strip=True)
                        drivers = [d.strip() for d in text.split("/") if d.strip()]
            else:
                # Single driver style: col 1 = driver cell
                driver_cell = cols[1] if len(cols) > 1 else None
                drivers = []
                team = ""

                if driver_cell:
                    all_links = [l for l in driver_cell.find_all("a") if l.get_text(strip=True)]
                    for link in all_links:
                        href = link.get("href", "")

                        # Extract driver name from span.name-short
                        name_span = link.find("span", class_="name-short")
                        if name_span:
                            drivers.append(name_span.get_text(strip=True))
                        
                        # Extract team from span.team
                        team_span = link.find("span", class_="team")
                        if team_span:
                            team = team_span.get_text(strip=True)

                        # Fallback if no specific spans found
                        if not drivers and not team:
                            if "/driver/" in href or "/rider/" in href:
                                drivers.append(link.get_text(strip=True))
                            elif "/team/" in href or "/constructor/" in href:
                                team = link.get_text(strip=True)
                            else:
                                text = link.get_text(strip=True)
                                if any(k in text.lower() for k in team_keywords):
                                    team = text
                                else:
                                    drivers.append(text)

                    if not drivers and not team:
                        text = driver_cell.get_text(separator="|", strip=True)
                        parts = [p.strip() for p in text.split("|") if p.strip()]
                        drivers = [parts[0]] if parts else [""]
                        team = parts[-1] if len(parts) > 1 else ""

            # Number
            num_col = number_col if number_col >= 0 else 2
            number = cols[num_col].get_text(strip=True) if len(cols) > num_col else ""

            # Laps, time, interval
            laps     = cols[laps_idx].get_text(strip=True) if laps_idx > 0 and len(cols) > laps_idx else ""
            time_val = cols[time_idx].get_text(strip=True) if time_idx > 0 and len(cols) > time_idx else ""
            interval = cols[int_idx].get_text(strip=True)  if int_idx  > 0 and len(cols) > int_idx  else ""

            # Clean interval
            if interval and len(interval) > 12:
                m = re.match(r'^([+\-]?[\d.:\']+(?:\s*[Ll]ap[s]?)?)', interval)
                if m:
                    interval = m.group(1).strip()

            # Add + prefix if missing
            if interval and not interval.startswith(('+', '-')) and 'lap' not in interval.lower():
                interval = '+' + interval

            result = {"position": position}

            # Single driver vs multi-driver
            if len(drivers) > 1:
                result["drivers"] = drivers
            elif drivers:
                result["driver"] = drivers[0]

            if team:      result["team"]     = team
            if number:    result["number"]   = number
            if laps:      result["laps"]     = laps

            if is_race:
                if position == 1 and time_val:
                    result["time"] = time_val.replace("'", ":")
                elif interval:
                    result["interval"] = interval
            else:
                # Practice/Qualifying: extract absolute lap time from TIME column
                # TIME column sometimes contains interval+time concatenated e.g. "+0.2971'29.607"
                # Extract the absolute time (contains ' separator like 1'29.607)
                if time_val:
                    # Find absolute time pattern: digits + ' + digits e.g. "1'29.607"
                    abs_time_match = re.search(r"\d+'\d+\.\d+", time_val)
                    if abs_time_match:
                        result["time"] = abs_time_match.group(0).replace("'", ":")
                    else:
                        result["time"] = time_val.replace("'", ":")

            results.append(result)

        except Exception as e:
            print(f"⚠️  Row error: {e}")
            continue

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",        required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--series",     required=True)
    args = parser.parse_args()

    json_file = SERIES_JSON.get(args.series)
    if not json_file:
        print(f"❌ Unknown series: {args.series}")
        sys.exit(1)

    if not Path(json_file).exists():
        print(f"❌ JSON not found: {json_file}")
        sys.exit(1)

    print(f"🔍 Fetching: {args.url}")
    html = fetch_page(args.url)

    print("📊 Parsing results...")
    results = parse_results(html, args.url)

    if len(results) < 3:
        print(f"❌ Too few results ({len(results)}), aborting")
        sys.exit(1)

    print(f"✅ Found {len(results)} results")

    # Load JSON and find session
    with open(json_file, "r", encoding="utf-8") as f:
        rounds = json.load(f)

    found = False
    for round_data in rounds:
        for session in round_data.get("sessions", []):
            if session.get("id") == args.session_id:
                session["resultsUrl"] = args.url
                session["results"] = results
                found = True
                print(f"✅ Updated session: {args.session_id}")
                break
        if found:
            break

    if not found:
        print(f"❌ Session not found: {args.session_id}")
        sys.exit(1)

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(rounds, f, indent=2, ensure_ascii=False)

    print(f"🎉 Saved to {json_file}")


if __name__ == "__main__":
    main()
