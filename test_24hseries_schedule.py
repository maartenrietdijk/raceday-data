import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import official_schedule_scanner as s

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/24hseries'
CAL=ROOT/'tests/fixtures/calendars'
REG=json.loads((ROOT/'session-time-sources.json').read_text())
CFG=next(x for x in REG['series'] if x['seriesId']=='24hseries')
EVENTS=json.loads((CAL/'24hseries_2027.json').read_text())

class Series24HTests(unittest.TestCase):
    def source(self,slug='michelin-24h-kyalami-2027'):
        return s.fixture_result(FIX/(slug+'.html'),'https://www.24hseries.com/races/'+slug)

    def test_real_kyalami_track_time_and_qualifying_block(self):
        rows=s.parse_24hseries_schedule(self.source(),EVENTS[0],2027,'Africa/Johannesburg')
        self.assertEqual([(x.name,x.local_time,x.duration_minutes) for x in rows],[('Free Practice','10:30',120),('Qualifying','14:45',190),('Night Practice','19:30',90)])
        self.assertEqual(s.editor_value(rows[0],'Europe/Amsterdam'),('2027-01-08','09:30','2027-01-08T08:30:00Z'))
        self.assertNotIn('Race',[x.name for x in rows]) # explicit Time TBC beats numeric attribute

    def test_real_unpublished_and_archived_pages_do_not_invent_times(self):
        for year in [2026,2027]:
            for event in json.loads((CAL/('24hseries_'+str(year)+'.json')).read_text()):
                if 'Kyalami' in event['raceName']:continue
                slug=event['officialScheduleUrl'].rsplit('/',1)[-1]
                with self.subTest(event=slug):
                    zone=s.resolve_event_timezone(REG,CFG,event)
                    self.assertEqual(s.parse_24hseries_schedule(self.source(slug),event,year,zone),[])

    def test_wrong_event_year_zone_fail_closed(self):
        for event,year,zone in [(EVENTS[1],2027,'Africa/Johannesburg'),(EVENTS[0],2026,'Africa/Johannesburg'),(EVENTS[0],2027,'Europe/Amsterdam')]:
            with self.assertRaises(s.SourceError):s.parse_24hseries_schedule(self.source(),event,year,zone)

    def test_calendar_six_rounds_tbc_europe_and_stable_ids(self):
        self.assertEqual(len(EVENTS),6)
        self.assertEqual([e['roundNumber'] for e in EVENTS],list(range(1,7)))
        self.assertEqual([e['sessions'][0]['date'] for e in EVENTS],['2027-01-08','2027-03-19','2027-04-09','2027-05-14','2027-06-04','2027-09-10'])
        self.assertTrue(all(x['timeLocal'] is None for e in EVENTS[1:] for x in e['sessions']))
        self.assertIsNone(EVENTS[0]['sessions'][-1]['timeLocal'])
        ids=[x['id'] for e in EVENTS for x in e['sessions']]
        self.assertEqual(len(ids),len(set(ids)))

    def test_full_scan_uses_manual_links_and_preserves_calendar(self):
        before=copy.deepcopy(EVENTS)
        def fetch(url,domains,extra_headers=None):
            self.assertTrue(s.allowed_url(url,domains))
            return self.source(url.rsplit('/',1)[-1])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'24hseries_2027.json';path.write_text(json.dumps(EVENTS));original=path.read_bytes()
            with patch.object(s,'fetch_url',side_effect=fetch):
                result=s.scan(root,REG,None,{'24hseries'},'2026-10-05T12:00:00Z',only_events={x['id'] for x in EVENTS},replace_filled=True)
            self.assertEqual(path.read_bytes(),original)
        self.assertEqual(EVENTS,before)
        self.assertFalse(any(x['status']=='open' for x in result['proposals']))
        self.assertEqual(len(result['sourcesUsed']),6)
        self.assertEqual(sum(x['status']=='unresolved' for x in result['sourcesUsed']),5)
        self.assertEqual([x['status'] for x in result['proposals']],['verified']*3)

    def test_confirmed_race_and_split_race_parts(self):
        source=self.source();body=source.body.replace(' (Time TBC)','')
        confirmed=s.FetchResult(source.stable_url,source.final_url,source.title,body,None)
        rows=s.parse_24hseries_schedule(confirmed,EVENTS[0],2027,'Africa/Johannesburg')
        self.assertEqual(rows[-1].name,'Race');self.assertEqual(rows[-1].duration_minutes,1440)
        body=body.replace('Start Michelin 24H KYALAMI 2027','Start Race Part 1')
        rows=s.parse_24hseries_schedule(s.FetchResult(source.stable_url,source.final_url,source.title,body,None),EVENTS[0],2027,'Africa/Johannesburg')
        self.assertEqual(rows[-1].name,'Race Part 1')
        self.assertEqual(s.session_number('Race Part 2'),2)

    def test_track_days_grid_and_finish_are_excluded(self):
        rows=s.parse_24hseries_schedule(self.source(),EVENTS[0],2027,'Africa/Johannesburg')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(x.date=='2027-01-08' for x in rows))

    def test_incomplete_qualifying_never_generates_partial_block(self):
        source=self.source();body=source.body.replace('Qualifying 2 - Class 992','Qualifying 2 - Class 992 (TBC)')
        rows=s.parse_24hseries_schedule(s.FetchResult(source.stable_url,source.final_url,source.title,body,None),EVENTS[0],2027,'Africa/Johannesburg')
        self.assertFalse(any(x.name=='Qualifying' for x in rows))

    def test_changed_time_and_published_race_target_existing_slots(self):
        source=self.source();body=source.body.replace('January 8, 2027 10:30','January 8, 2027 10:45').replace('data-time="10:30"','data-time="10:45"').replace(' (Time TBC)','')
        rows=s.parse_24hseries_schedule(s.FetchResult(source.stable_url,source.final_url,source.title,body,None),EVENTS[0],2027,'Africa/Johannesburg')
        proposals=s.build_event_proposals(CFG,'24hseries_2027.json',EVENTS[0],source,rows,'Europe/Amsterdam','2026-10-05T12:00:00Z',replace_filled=True)
        active=[x for x in proposals if x['status']=='open']
        self.assertEqual([x['sessionName'] for x in active],['Free Practice','Race'])
        self.assertTrue(all(x['proposalType']=='time-update' and x['sessionId'] for x in active))

    def test_ambiguous_legacy_numbered_qualifying_requires_review(self):
        event=copy.deepcopy(EVENTS[0]);event['sessions']=[dict(name='Qualifying 1',kind='qualifying'),dict(name='Qualifying 2',kind='qualifying')]
        with self.assertRaisesRegex(s.SourceError,'unambiguously'):
            s.parse_24hseries_schedule(self.source(),event,2027,'Africa/Johannesburg')
