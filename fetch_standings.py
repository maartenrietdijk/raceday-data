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
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SERIES_JSON = {
    "f1":              "f1_standings_2026.json",
    "f2":              "f2_standings_2026.json",
    "f3":              "f3_standings_2026.json",
    "f1academy":       "f1academy_standings_2026.json",
    "formulae":        "formulae_standings_2026.json",
    "motogp":          "motogp_standings_2026.json",
    "moto2":           "moto2_standings_2026.json",
    "moto3":           "moto3_standings_2026.json",
    "wsbk":            "wsbk_standings_2026.json",
    "indycar":         "indycar_standings_2026.json",
    "nascar":          "nascar_standings_2026.json",
    "nascar_oreilly":  "nascar_oreilly_standings_2026.json",
    "nascar_trucks":   "nascar_trucks_standings_2026.json",
    "wec":             "wec_standings_2026.json",
    "imsa":            "imsa_standings_2026.json",
    "elms":            "elms_standings_2026.json",
    "alms":            "alms_standings_2026.json",
    "gtwce":           "gtwce_standings_2026.json",
    "dtm":             "dtm_standings_2026.json",
    "wrc":             "wrc_standings_2026.json",
    "supercars":       "supercars_standings_2026.json",
    "british_gt":      "british_gt_standings_2026.json",
}

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


def parse_standings(html: str) -> list[dict]:
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

        if driver_col is None or pts_col is None:
            continue  # not a standings table

        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) <= max(filter(None, [pos_col, driver_col, team_col, pts_col])):
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
            name = clean_name(name)
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
            pts_text = re.sub(r"[^\d.]", "", pts_text)
            try:
                pts = int(float(pts_text)) if pts_text else 0
            except ValueError:
                pts = 0

            entries.append({"position": pos, "name": name, "team": team, "points": pts})

    if entries:
        entries.sort(key=lambda x: x["position"])
        return entries

    # Fallback: look for structured list items (motorsport.com sometimes renders standings
    # as div-based grids rather than <table>)
    for container in soup.find_all(["ul", "ol", "div"], limit=20):
        items = container.find_all(["li", "div"], recursive=False)
        if len(items) < 3:
            continue
        candidate = []
        for item in items:
            text = item.get_text(" ", strip=True)
            # Heuristic: line should contain a number, a name-like string, and points
            nums = re.findall(r"\d+", text)
            if len(nums) >= 2:
                pos_match = re.match(r"^(\d+)", text.strip())
                pts_match = re.search(r"(\d+)\s*(?:pts?|points?)?$", text.strip(), re.IGNORECASE)
                if pos_match and pts_match:
                    candidate.append({
                        "position": int(pos_match.group(1)),
                        "name": "",
                        "team": "",
                        "points": int(pts_match.group(1)),
                    })
        if len(candidate) >= 5:
            entries = candidate
            break

    return entries


def main():
    parser = argparse.ArgumentParser(description="Fetch motorsport.com standings")
    parser.add_argument("--url",    required=True, help="Standings URL")
    parser.add_argument("--series", required=True, help="Series ID (e.g. f1)")
    args = parser.parse_args()

    output_file = SERIES_JSON.get(args.series)
    if not output_file:
        output_file = f"{args.series}_standings_2026.json"

    print(f"Fetching {args.url}…")
    html = get_html(args.url)

    entries = parse_standings(html)
    if not entries:
        print("⚠️  No standings found in page. The page may be JavaScript-rendered.", file=sys.stderr)
        print("   Saving empty standings file so the app shows no data (not a crash).", file=sys.stderr)
        entries = []

    out = Path(output_file)
    out.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    print(f"✓ Saved {len(entries)} entries to {output_file}")

    if not entries:
        sys.exit(1)  # Signal to CI that scraping failed


if __name__ == "__main__":
    main()
