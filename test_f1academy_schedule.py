import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import official_schedule_scanner as s

ROOT=Path(__file__).resolve().parents[1]
CALENDARS=ROOT/'tests/fixtures/calendars'
FIXTURES=ROOT/'tests/fixtures/f1academy'
SOURCES=json.loads((FIXTURES/'sources.json').read_text())
CALENDAR='https://www.f1academy.com/Racing-Series/Calendar'
NOW='2026-10-03T14:00:00Z'

def fetch(url,domains,extra_headers=None):
    assert s.allowed_url(url,domains)
    body=(FIXTURES/SOURCES[url]).read_text()
    return s.FetchResult(url,url,s.page_title(body),body,None)

class AcademyTests(unittest.TestCase):
    def setUp(self):
        self.registry=json.loads((ROOT/'session-time-sources.json').read_text())
        self.cfg=next(x for x in self.registry['series'] if x['seriesId']=='f1academy')
        self.events=json.loads((CALENDARS/'f1academy_2026.json').read_text())

    def source(self,round_number):
        return fetch(CALENDAR if round_number==0 else CALENDAR.replace('Calendar','Results?raceid=')+str([22,24,25,26,27,28][round_number-1]),self.cfg['allowedDomains'])

    def parse(self,round_number,source=None,zone=None):
        event=self.events[round_number-1]
        return s.parse_academy_schedule(source or self.source(round_number),event,2026,zone or s.resolve_event_timezone(self.registry,self.cfg,event))

    def mutated(self,round_number,modify):
        source=self.source(round_number)
        data=s.academy_page_data(source)
        modify(data)
        body='<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':{'pageData':data}}})+'</script>'
        return s.FetchResult(source.stable_url,source.final_url,source.title,body,None)

    def test_austin_five_live_sessions_and_european_dst_change(self):
        sessions=self.parse(5)
        self.assertEqual([x.name for x in sessions],['Free Practice','Qualifying','Opening Race','Reverse Grid Race','Feature Race'])
        self.assertEqual([s.editor_value(x,'Europe/Amsterdam') for x in sessions],[
            ('2026-10-22','19:25','2026-10-22T17:25:00Z'),
            ('2026-10-22','23:30','2026-10-22T21:30:00Z'),
            ('2026-10-24','00:50','2026-10-23T22:50:00Z'),
            ('2026-10-24','21:45','2026-10-24T19:45:00Z'),
            ('2026-10-25','16:40','2026-10-25T15:40:00Z')])
        self.assertEqual([x.duration_minutes for x in sessions],[40,30,35,35,30])

    def test_unconfirmed_las_vegas_placeholders_ignored_before_offset_check(self):
        with self.assertRaisesRegex(s.SourceError,'placeholder timestamps'):self.parse(6)
        self.assertTrue(all(x['Unconfirmed'] for x in s.academy_page_data(self.source(6))['SessionResults']))
        # These deliberately carry -07 even though Las Vegas is -08 in November.
        with self.assertRaises(s.SourceError):self.parse(6,self.mutated(6,lambda d:d['SessionResults'][0].update(Unconfirmed=False)))

    def test_exact_season_round_circuit_discovery_all_six_rounds(self):
        calendar=self.source(0)
        for n,event in enumerate(self.events,1):
            self.assertEqual(s.discover_academy_event_url(calendar,event,2026),self.source(n).final_url)
            # Calendar and event page expose the same confirmed schedule.
            zone=s.resolve_event_timezone(self.registry,self.cfg,event)
            if n == 6:
                with self.assertRaises(s.SourceError):s.parse_academy_schedule(calendar,event,2026,zone)
            else:
                self.assertEqual(s.parse_academy_schedule(calendar,event,2026,zone),self.parse(n))
        with self.assertRaises(s.SourceError):s.discover_academy_event_url(calendar,self.events[4],2025)
        wrong=dict(self.events[4],circuitName='Silverstone Circuit')
        with self.assertRaises(s.SourceError):s.discover_academy_event_url(calendar,wrong,2026)

    def test_no_results_laptimes_countdown_or_testing_sessions(self):
        self.assertEqual([len(self.parse(n)) for n in range(1,6)],[4,5,4,4,5])
        modified=self.mutated(5,lambda d:d['SessionResults'].append(dict(d['SessionResults'][0],SessionName='Testing',SessionType='TESTING')))
        self.assertEqual(len(self.parse(5,modified)),5)

    def test_shanghai_montreal_silverstone_and_zandvoort_zones(self):
        self.assertEqual(s.editor_value(self.parse(1)[0],'Europe/Amsterdam'),('2026-03-13','02:10','2026-03-13T01:10:00Z'))
        self.assertEqual(s.editor_value(self.parse(2)[1],'Europe/Amsterdam'),('2026-05-23','00:18','2026-05-22T22:18:00Z'))
        self.assertEqual(s.editor_value(self.parse(3)[0],'Europe/Amsterdam')[:2],('2026-07-03','08:45'))
        self.assertEqual(s.editor_value(self.parse(4)[0],'Europe/Amsterdam')[:2],('2026-08-21','14:00'))
        self.assertEqual(self.registry['editorTimeZones']['f1academy'],'Europe/Amsterdam')

    def test_one_physical_montreal_qualifying_for_two_grid_classifications(self):
        sessions=self.parse(2)
        self.assertEqual([x.name for x in sessions if 'Qualifying' in x.name],['Qualifying'])
        changed=self.mutated(2,lambda d:d['SessionResults'][2].update(SessionStartTime='2026-05-22T19:00:00-04:00',SessionEndTime='2026-05-22T19:30:00-04:00'))
        self.assertEqual([x.name for x in self.parse(2,changed) if 'Qualifying' in x.name],['Qualifying 1','Qualifying 2'])

    def test_wrong_offset_missing_confirmation_and_unknown_zone(self):
        for changes in [dict(SessionStartTime='2026-10-22T12:25:00-06:00'),dict(SessionStartTime='2026-10-22T12:25:00'),dict(SessionStartTime='not-a-date'),dict(SessionEndTime='2026-10-22T11:00:00-05:00')]:
            with self.assertRaises(s.SourceError):self.parse(5,self.mutated(5,lambda d:d['SessionResults'][0].update(changes)))
        modified=self.mutated(5,lambda d:d['SessionResults'][0].pop('Unconfirmed'))
        self.assertEqual(len(self.parse(5,modified)),4)
        with self.assertRaises(s.SourceError):self.parse(5,zone='Unknown/Zone')

    def test_dst_windows_different_us_and_europe_switch_dates(self):
        cases=[('2026-03-07T12:00:00-06:00','2026-03-07','19:00'),('2026-03-15T12:00:00-05:00','2026-03-15','18:00'),('2026-04-01T12:00:00-05:00','2026-04-01','19:00'),('2026-10-24T12:00:00-05:00','2026-10-24','19:00'),('2026-10-25T12:00:00-05:00','2026-10-25','18:00'),('2026-11-02T12:00:00-06:00','2026-11-02','19:00')]
        for timestamp,day,expected in cases:
            changed=self.mutated(5,lambda d:d.update(RaceStartDate=day,RaceEndDate=day,SessionResults=[dict(d['SessionResults'][0],SessionStartTime=timestamp,SessionEndTime=None)]))
            event=dict(self.events[4],sessions=[dict(self.events[4]['sessions'][0],date=day)])
            session=s.parse_academy_schedule(changed,event,2026,'America/Chicago')[0]
            self.assertEqual(s.editor_value(session,'Europe/Amsterdam')[:2],(day,expected))

    def test_dst_gap_and_fold_offset_validation(self):
        def parse(timestamp):
            day=timestamp[:10]
            src=self.mutated(5,lambda d:d.update(RaceStartDate=day,RaceEndDate=day,SessionResults=[dict(d['SessionResults'][0],SessionStartTime=timestamp,SessionEndTime=None)]))
            event=dict(self.events[4],sessions=[dict(self.events[4]['sessions'][0],date=day)])
            return s.parse_academy_schedule(src,event,2026,'America/Chicago')[0]
        with self.assertRaises(s.SourceError):parse('2026-03-08T02:30:00-06:00')
        first=parse('2026-11-01T01:30:00-05:00');second=parse('2026-11-01T01:30:00-06:00')
        self.assertEqual(s.editor_value(first,'Europe/Amsterdam')[2],'2026-11-01T06:30:00Z')
        self.assertEqual(s.editor_value(second,'Europe/Amsterdam')[2],'2026-11-01T07:30:00Z')

    def test_confirmed_las_vegas_evening_moves_to_next_day(self):
        src=self.mutated(6,lambda d:d.update(SessionResults=[dict(d['SessionResults'][0],Unconfirmed=False,SessionStartTime='2026-11-19T19:00:00-08:00',SessionEndTime='2026-11-19T19:40:00-08:00')]))
        self.assertEqual(s.editor_value(self.parse(6,src)[0],'Europe/Amsterdam'),('2026-11-20','04:00','2026-11-20T03:00:00Z'))

    def test_legacy_races_reuse_ids_and_proposals_are_idempotent(self):
        event=self.events[4]
        with patch.object(s,'fetch_url',side_effect=fetch):
            result=s.scan(CALENDARS,self.registry,None,{'f1academy'},NOW,only_events={event['id']},replace_filled=True)
        proposals=result['proposals']
        self.assertEqual(len(proposals),5)
        self.assertTrue(all(x['status']=='open' and x['proposalType']=='time-update' for x in proposals))
        self.assertEqual({x['sessionId'] for x in proposals},{x['id'] for x in event['sessions']})
        self.assertEqual(next(x for x in proposals if x['sourceSessionName']=='Opening Race')['proposed']['date'],'2026-10-24')
        accepted=json.loads(json.dumps(event))
        for item in accepted['sessions']:item.update(next(x['proposed'] for x in proposals if x['sessionId']==item['id']))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'f1academy_2026.json').write_text(json.dumps([accepted]))
            with patch.object(s,'fetch_url',side_effect=fetch):repeat=s.scan(root,self.registry,None,{'f1academy'},NOW,replace_filled=True)
        self.assertEqual(len(repeat['proposals']),5)
        self.assertTrue(all(x['status']=='verified' for x in repeat['proposals']))

    def test_manual_url_wrong_round_and_date_rollover(self):
        event=json.loads(json.dumps(self.events[4]));event['officialScheduleUrl']=self.source(5).final_url
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'f1academy_2026.json').write_text(json.dumps([event]))
            with patch.object(s,'fetch_url',side_effect=fetch):result=s.scan(root,self.registry,None,{'f1academy'},NOW,only_events={event['id']},replace_filled=True)
            self.assertEqual(result['sourcesUsed'][0]['status'],'ok')
            event['officialScheduleUrl']=self.source(3).final_url;(root/'f1academy_2026.json').write_text(json.dumps([event]))
            with patch.object(s,'fetch_url',side_effect=fetch):result=s.scan(root,self.registry,None,{'f1academy'},NOW,only_events={event['id']},replace_filled=True)
            self.assertEqual(result['sourcesUsed'][0]['status'],'unresolved')

    def test_partial_confirmation_cannot_renumber_existing_races(self):
        src=self.mutated(5,lambda d:d['SessionResults'][2].update(Unconfirmed=True))
        sessions=self.parse(5,src)
        official=next(x for x in sessions if x.name=='Reverse Grid Race')
        matched,conflict=s.match_academy_session(official,sessions,self.events[4]['sessions'])
        self.assertIsNone(matched)
        self.assertIsNotNone(conflict)

    def test_upcoming_filled_weekend_gets_scheduled_refresh(self):
        event=json.loads(json.dumps(self.events[4]))
        for item in event['sessions']:item['timeLocal']='12:00'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'f1academy_2026.json').write_text(json.dumps([event]))
            self.assertEqual(len(s.calendar_inventory(root,{'f1academy'},checked_at=datetime(2026,10,3,tzinfo=timezone.utc))),1)
            with patch.object(s,'fetch_url',side_effect=fetch):result=s.scan(root,self.registry,None,{'f1academy'},NOW)
            self.assertEqual(len(result['proposals']),5)
            self.assertTrue(all(x['status']=='open' for x in result['proposals']))

if __name__=='__main__':unittest.main()
