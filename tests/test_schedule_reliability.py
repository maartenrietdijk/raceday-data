import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import official_schedule_scanner as s

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'session-time-sources.json').read_text())

class ReliabilityTests(unittest.TestCase):
    def slot(self, name, day='2026-10-09', clock='12:00', id='slot'):
        return dict(id=id, name=name, kind=s.normalize_kind(name), date=day, timeLocal=clock, durationMinutes=30)

    def build(self, slots, sources, zone='Europe/Amsterdam', cfg=None):
        return s.build_event_proposals(cfg or dict(seriesId='dtm', name='DTM', sourceKind='dtm-api'), 'test.json', dict(id='event', sessions=slots), s.FetchResult('https://example.com','https://example.com','', '', None), sources, zone, '2026-10-05T10:00:00Z', replace_filled=True)

    def source(self, name, day='2026-10-09', clock='12:00', zone='Europe/Amsterdam', duration=30):
        return s.SourceSession(name, day, clock, zone, duration)

    def test_all_registered_calendar_sessions_match_their_own_slot_or_require_review(self):
        seen=set(); count=0
        for cfg in REGISTRY['series']:
            for path in ROOT.glob(cfg['seriesId'] + '_20*.json'):
                for event in json.loads(path.read_text()):
                    for slot in event.get('sessions', []):
                        if s.normalize_kind(slot.get('name','')) not in s.SUPPORTED_KINDS:
                            continue
                        with self.subTest(file=path.name, event=event['id'], session=slot['name']):
                            match, conflict = s.match_session(self.source(slot['name'], slot.get('date') or '2026-10-09'), event['sessions'])
                            self.assertTrue(conflict or match is slot, (slot,match))
                        seen.add(cfg['seriesId']); count+=1
        self.assertEqual(seen, {x['seriesId'] for x in REGISTRY['series']})
        self.assertGreater(count, 3000)

    def test_distinct_subtypes_never_replace_each_other(self):
        for source, existing in [('Warm Up','Practice 1'), ('Superpole Race','Race 2'), ('Top Ten Shootout 1','Qualifying 1'), ('Qualifying Group A','Qualifying Group B'), ('GT3 Qualifying 1','GT4 Qualifying 1'), ('Hyperpole 2 - Hypercar','Hyperpole 2 - LMP2 & LMGT3'), ('Night Practice','Practice 2'), ('Pre Qualifying','Practice 1'), ('Top 12 Qualifying','Fast 6 Qualifying')]:
            with self.subTest(source=source,existing=existing):
                match, _ = s.match_session(self.source(source), [self.slot(existing)])
                self.assertIsNone(match)

    def test_session_numbers_ignore_class_and_duration(self):
        for name, number in [('GT3 Qualifying 2',2),('6 Hour Race',None),('SS12 - Road 1',12),('Race 2',2),('Qualifying Group A',None)]:
            self.assertEqual(s.session_number(name),number)

    def test_duplicate_calendar_sessions_require_review(self):
        rows=self.build([self.slot('Practice 1'),self.slot('Practice 1',id='other')],[self.source('Practice 1')])
        self.assertTrue(all(x['status']=='requires_review' for x in rows))
        self.assertTrue(all(x['proposed'] is None for x in rows))

    def test_duplicate_source_rows_do_not_create_new_sessions(self):
        source=self.source('Practice 1')
        rows=self.build([self.slot('Practice 1')],[source,source])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['status'],'verified')

    def test_conflicting_source_rows_require_review(self):
        rows=self.build([self.slot('Practice 1')],[self.source('Practice 1'),self.source('Practice 1',clock='12:10')])
        self.assertTrue(all(x['status']=='requires_review' and x['proposed'] is None for x in rows))

    def test_date_matching_uses_editor_timezone(self):
        slots=[self.slot('Race', day='2026-10-09',id='first'),self.slot('Race',day='2026-10-10',id='second')]
        rows=self.build(slots,[self.source('Race',day='2026-10-10',clock='01:00',zone='Asia/Tokyo')])
        self.assertEqual(rows[0]['sessionId'],'first')
        self.assertEqual(rows[0]['proposed']['date'],'2026-10-09')

    def test_cancelled_session_is_not_recreated(self):
        rows=self.build([self.slot('Qualifying (cancelled)')],[self.source('Qualifying')])
        self.assertEqual(rows[0]['status'],'requires_review')
        self.assertIsNone(rows[0]['proposed'])

    def test_dst_gap_fold_and_conflicting_utc_evidence(self):
        for source in [self.source('Race','2026-03-29','02:30'), self.source('Race','2026-10-25','02:30'),s.SourceSession('Race','2026-10-09','12:00','Europe/Amsterdam',30,'12:00')]:
            with self.assertRaises(s.SourceError): s.editor_value(source,'Europe/Amsterdam')
        value=s.editor_value(s.SourceSession('Race','2026-10-25','02:30','Europe/Amsterdam',30,'01:30'),'UTC')
        self.assertEqual(value[1],'01:30')

    def test_targeted_scan_retains_other_series_and_events(self):
        previous=[dict(fingerprint='a',seriesId='dtm',eventId='selected'),dict(fingerprint='b',seriesId='dtm',eventId='other'),dict(fingerprint='c',seriesId='f1',eventId='other')]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'session-time-sources.json').write_text(json.dumps(REGISTRY)); output=root/'proposals.json'
            output.write_text(json.dumps(dict(proposals=previous,sourcesUsed=previous)))
            with patch.object(s,'scan',return_value=dict(proposals=[],sourcesUsed=[])):
                s.main(['--root',str(root),'--output',str(output),'--series','dtm','--event','selected'])
            self.assertEqual([x['fingerprint'] for x in json.loads(output.read_text())['proposals']],['b','c'])
            self.assertEqual([x['fingerprint'] for x in json.loads(output.read_text())['sourcesUsed']],['b','c'])

    def test_every_registered_timezone_roundtrips(self):
        zones=set(REGISTRY['editorTimeZones'].values())|set(REGISTRY['eventTimeZones'].values())
        for zone in zones:
            for day in ['2026-01-15','2026-07-15','2026-10-05']:
                with self.subTest(zone=zone,day=day):
                    self.assertEqual(s.editor_value(self.source('Race',day,'12:30',zone),'%s'%zone)[:2],(day,'12:30'))

    def test_browser_local_column_never_used_as_track_time(self):
        for headers, cells, expected in [('My Time</th><th>Track Time','20:00</td><td>12:00','12:00'),('My Time','20:00',None)]:
            body='<h2>9 October 2026</h2><table><tr><th>Session</th><th>'+headers+'</th></tr><tr><td>Practice</td><td>'+cells+'</td></tr></table>'
            rows=s.parse_official_tables(s.FetchResult('', '', '', body, None),2026,'Europe/Amsterdam')
            self.assertEqual(rows[0].local_time if rows else None,expected)

    def test_name_only_change_is_not_a_time_correction(self):
        rows=self.build([self.slot('Practice')],[self.source('Free Practice 1')])
        self.assertEqual(rows[0]['status'],'verified')
        self.assertEqual(rows[0]['proposed']['name'],'Practice')
