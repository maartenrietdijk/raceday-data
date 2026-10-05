"""Regression cases with fixed inputs, independent of editable calendars.

The official and existing times are separate: accepting a real calendar
proposal must never change what this regression test is exercising.
"""
import copy
import unittest
import official_schedule_scanner as s

DTM_ZONE = 'Europe/Amsterdam'
DTM_EXISTING = [
    ('Practice 1', 'practice', '2026-10-09', '12:05', 55),
    ('Practice 2', 'practice', '2026-10-09', '15:30', 45),
    ('Qualifying 1', 'qualifying', '2026-10-10', '09:35', 20),
    ('Race 1', 'race', '2026-10-10', '13:30', 60),
    ('Qualifying 2', 'qualifying', '2026-10-11', '09:35', 20),
    ('Race 2', 'race', '2026-10-11', '16:30', 60),
]
DTM_OFFICIAL = [
    s.SourceSession('Free Practice 1', '2026-10-09', '12:05', DTM_ZONE, 55),
    s.SourceSession('Free Practice 2', '2026-10-09', '15:30', DTM_ZONE, 45),
    s.SourceSession('Qualifying 1', '2026-10-10', '09:35', DTM_ZONE, 20),
    s.SourceSession('Race 1', '2026-10-10', '13:30', DTM_ZONE, 60),
    s.SourceSession('Qualifying 2', '2026-10-11', '09:30', DTM_ZONE, 20),
    s.SourceSession('Race 2', '2026-10-11', '16:30', DTM_ZONE, 60),
]


def event_fixture(series, event_id, definitions):
    return dict(id=event_id, seriesId=series, sessions=[
        dict(id=f'{event_id}-s{i}', name=name, kind=kind, date=day,
             timeLocal=clock, durationMinutes=duration)
        for i, (name, kind, day, clock, duration) in enumerate(definitions, 1)
    ])


class SessionAliasTests(unittest.TestCase):
    def dtm_event(self):
        return event_fixture('dtm', 'dtm-2026-r08', DTM_EXISTING)

    def proposals(self, event, official, zone, source_kind):
        series = event['seriesId']
        source = s.FetchResult('https://example.com', 'https://example.com', '', '', None)
        return s.build_event_proposals(
            dict(seriesId=series, name=series, sourceKind=source_kind),
            series + '_2026.json', event, source, official, zone,
            '2026-10-05T00:00:00Z', replace_filled=True)

    def dtm_proposals(self, event):
        return self.proposals(event, DTM_OFFICIAL, DTM_ZONE, 'dtm-api')

    def test_dtm_only_real_time_change(self):
        event = self.dtm_event()
        before = copy.deepcopy(event)
        rows = self.dtm_proposals(event)
        changes = [r for r in rows if r['status'] == 'open']
        self.assertEqual([r['sessionName'] for r in changes], ['Qualifying 2'])
        self.assertEqual(changes[0]['proposalType'], 'time-update')
        self.assertEqual(changes[0]['sessionId'], 'dtm-2026-r08-s5')
        self.assertEqual(changes[0]['current']['timeLocal'], '09:35')
        self.assertEqual(changes[0]['proposed']['timeLocal'], '09:30')
        self.assertEqual([r['status'] for r in rows[:2]], ['verified', 'verified'])
        self.assertEqual(event, before)

    def test_dtm_already_correct_time_has_no_correction(self):
        event = self.dtm_event()
        event['sessions'][4]['timeLocal'] = '09:30'
        rows = self.dtm_proposals(event)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r['status'] == 'verified' for r in rows))
        self.assertFalse(any(r['proposalType'] in {'new-session', 'time-update'} for r in rows))

    def test_dtm_correction_recheck_preserves_slots(self):
        event = self.dtm_event()
        changes = [r for r in self.dtm_proposals(event) if r['status'] == 'open']
        for change in changes:
            target = next(slot for slot in event['sessions'] if slot['id'] == change['sessionId'])
            target.update({key: change['proposed'][key] for key in ['name', 'kind', 'date', 'timeLocal', 'durationMinutes']})
        first = self.dtm_proposals(event)
        second = self.dtm_proposals(event)
        self.assertEqual(first, second)
        self.assertTrue(all(r['status'] == 'verified' for r in first))
        self.assertEqual([r['sessionId'] for r in first], [slot['id'] for slot in event['sessions']])

    def test_bathurst_qualifying_shootout_and_race_exist(self):
        zone = 'Australia/Sydney'
        definitions = [
            ('Qualifying', 'qualifying', '2026-10-09', '16:10', 40),
            ('Top Ten Shootout', 'qualifying', '2026-10-10', '17:05', 50),
            ('Race', 'race', '2026-10-11', '11:30', 420),
        ]
        event = event_fixture('supercars', 'supercars-2026-11', definitions)
        official = [
            s.SourceSession('Boost Mobile Qualifying (Race 30)', '2026-10-09', '16:10', zone, 40),
            s.SourceSession('Boost Mobile Top Ten Shootout (Race 30)', '2026-10-10', '17:05', zone, 50),
            s.SourceSession('Race 30', '2026-10-11', '11:30', zone, 420),
        ]
        rows = self.proposals(event, official, zone, 'supercars-embedded-schedule')
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['status'] == 'verified' for r in rows))
        self.assertEqual([r['sessionId'] for r in rows], [slot['id'] for slot in event['sessions']])

    def test_numbered_sessions_remain_distinct(self):
        self.assertNotEqual(s.session_alias('Qualifying 1'), s.session_alias('Qualifying 2'))
        self.assertNotEqual(s.session_alias('Qualifying'), s.session_alias('Top Ten Shootout'))
        slot = {'id': 'a', 'name': 'Qualifying', 'kind': 'qualifying'}
        match, conflict = s.match_session(
            s.SourceSession('Qualifying 1', '2026-10-09', '16:10', 'Australia/Sydney'),
            [slot, dict(slot, id='b')])
        self.assertIsNone(match)
        self.assertIsNotNone(conflict)
