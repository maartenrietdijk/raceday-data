import json
import unittest
from pathlib import Path
import official_schedule_scanner as s

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / 'tests/fixtures/calendars'

class SessionAliasTests(unittest.TestCase):
    def proposals(self, series, event_name, names, zone, source_kind):
        event = next(e for e in json.loads((CAL / (series + '_2026.json')).read_text()) if event_name in e['raceName'])
        if series == 'dtm':
            # Fix the regression baseline even after the real proposal is accepted.
            next(slot for slot in event['sessions'] if slot['name'] == 'Qualifying 2')['timeLocal'] = '09:35'
        official = [s.SourceSession(name, slot['date'], '09:30' if name == 'Qualifying 2' else slot['timeLocal'], zone, slot['durationMinutes']) for slot, name in zip(event['sessions'], names)]
        source = s.FetchResult('https://example.com', 'https://example.com', '', '', None)
        return s.build_event_proposals({'seriesId': series, 'name': series, 'sourceKind': source_kind}, series + '_2026.json', event, source, official, zone, '2026-10-05T00:00:00Z', replace_filled=True)

    def test_dtm_only_real_time_change(self):
        rows = self.proposals('dtm', 'Hockenheim', ['Free Practice 1', 'Free Practice 2', 'Qualifying 1', 'Race 1', 'Qualifying 2', 'Race 2'], 'Europe/Amsterdam', 'dtm-api')
        self.assertEqual([r['sessionName'] for r in rows if r['status'] == 'open'], ['Qualifying 2'])

    def test_bathurst_qualifying_shootout_and_race_exist(self):
        names = ['Practice 1', 'Practice 2', 'Practice 3', 'Practice 4', 'Boost Mobile Qualifying (Race 30)', 'Practice 5', 'Practice 6', 'Boost Mobile Top Ten Shootout (Race 30)', 'Practice 7', 'Warm Up', 'Race 30']
        rows = self.proposals('supercars', 'Bathurst', names, 'Australia/Sydney', 'supercars-embedded-schedule')
        for title in [names[4], names[7], names[-1]]:
            row = next(r for r in rows if r['sourceSessionName'] == title)
            self.assertEqual(row['status'], 'verified')
            self.assertIsNotNone(row['sessionId'])

    def test_numbered_sessions_remain_distinct(self):
        self.assertNotEqual(s.session_alias('Qualifying 1'), s.session_alias('Qualifying 2'))
        self.assertNotEqual(s.session_alias('Qualifying'), s.session_alias('Top Ten Shootout'))
        slot = {'id': 'a', 'name': 'Qualifying', 'kind': 'qualifying'}
        match, conflict = s.match_session(s.SourceSession('Qualifying 1', '2026-10-09', '16:10', 'Australia/Sydney'), [slot, dict(slot, id='b')])
        self.assertIsNone(match)
        self.assertIsNotNone(conflict)
