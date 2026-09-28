#!/usr/bin/env python3
"""Refresh standings at most once per 3-hour slot, within 18h of a race start."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_standings import MOTORSPORT_SERIES, fetch_series

STATE_PATH = Path('.raceday/auto-standings-state.json')
WINDOW_HOURS = 18
SLOT_HOURS = 3
MAX_CHECKS = 6
# Same timeLocal convention as the calendar app and auto_fetch_results.py.
SERIES_TIMEZONES = {
    'nascar': 'America/New_York', 'nascar_oreilly': 'America/New_York',
    'nascar_trucks': 'America/New_York', 'indycar': 'America/New_York',
    'indynxt': 'America/New_York', 'imsa': 'America/New_York',
    'supercars': 'Australia/Sydney',
}


def session_start(session, series):
    if session.get('_tbcMode'):
        return None
    try:
        if session.get('dateUTC'):
            start = datetime.fromisoformat(session['dateUTC'].replace('Z', '+00:00'))
            # A date without a known time must never start a refresh window.
            if 'T' not in session['dateUTC']:
                return None
            return start.replace(tzinfo=start.tzinfo or timezone.utc).astimezone(timezone.utc)
        if not session.get('date') or not session.get('timeLocal'):
            return None
        zone = ZoneInfo(SERIES_TIMEZONES.get(series, os.environ.get('RACEDAY_TIMEZONE', 'Europe/Amsterdam')))
        start = datetime.fromisoformat(f"{session['date']}T{session['timeLocal']}")
        return start.replace(tzinfo=start.tzinfo or zone).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def race_sessions(round_data, series):
    sessions = round_data.get('sessions', [])
    if series == 'wrc':
        # One rally is one standings trigger, not one trigger per special stage.
        stages = [s for s in sessions if s.get('kind') == 'stage']
        if not stages or any(session_start(s, series) is None for s in stages):
            return []
        return [max(stages, key=lambda s: session_start(s, series))]
    return [s for s in sessions if s.get('kind') in ('race', 'sprintRace')]


def due_jobs(now, state, root=Path('.'), years=None):
    now = now.astimezone(timezone.utc)
    years = years if years is not None else (now.year - 1, now.year)
    grouped = {}
    for year in years:
        for series, slug in MOTORSPORT_SERIES.items():
            calendar = root / f'{series}_{year}.json'
            if not calendar.exists():
                continue
            rounds = json.loads(calendar.read_text())
            for round_data in rounds:
                for session in race_sessions(round_data, series):
                    start = session_start(session, series)
                    if start is None or not session.get('id') or not round_data.get('id'):
                        continue
                    elapsed = (now - start).total_seconds() / 3600
                    if elapsed < 0 or elapsed > WINDOW_HOURS:
                        continue
                    slot = min(int(elapsed // SLOT_HOURS), MAX_CHECKS - 1)
                    key = f"{series}:{year}:{round_data['id']}:{session['id']}"
                    checked = state.get('races', {}).get(key, {}).get('slots', [])
                    if slot in checked or len(checked) >= MAX_CHECKS:
                        continue
                    job = grouped.setdefault((series, year), {
                        'series': series, 'url': f'https://www.motorsport.com/{slug}/standings/{year}/',
                        'races': [],
                    })
                    job['races'].append({'key': key, 'slot': slot, 'start': start,
                                         'roundId': round_data['id'], 'roundName': round_data.get('raceName', '')})
    return list(grouped.values())


def save_state(state, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def run(now=None, root=Path('.'), state_path=STATE_PATH, years=None, dry_run=False):
    now = now or datetime.now(timezone.utc)
    state = json.loads(state_path.read_text()) if state_path.exists() else {'version': 1, 'races': {}}
    jobs = due_jobs(now, state, root, years)
    if not jobs:
        print('No standings due: no network requests.')
        return 0
    failures = 0
    for job in jobs:
        latest = max(job['races'], key=lambda race: race['start'])
        print(f"{job['series']}: standings due within the 18-hour race window")
        if dry_run:
            continue
        # Count attempts, including failures. Skipped slots are never caught up.
        for race in job['races']:
            record = state.setdefault('races', {}).setdefault(race['key'], {'slots': []})
            record['slots'].append(race['slot'])
            record['lastAttemptAt'] = now.isoformat()
        save_state(state, state_path)
        try:
            fetch_series(job['url'], job['series'], latest['roundId'], latest['roundName'])
        except Exception as exc:
            failures += 1
            print(f"WARNING: {job['series']}: {exc}")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(years=[args.year] if args.year else None, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
