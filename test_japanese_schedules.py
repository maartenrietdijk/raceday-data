import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
import official_schedule_scanner as s

ROOT = Path(__file__).resolve().parents[1]
CALENDARS = ROOT / 'tests/fixtures/calendars'
FIXTURES = ROOT / 'tests/fixtures/japanese'
URLS = json.loads((FIXTURES/'sources.json').read_text())
NOW = '2026-10-03T12:00:00Z'


def fetch(url, domains, extra_headers=None):
    if not s.allowed_url(url, domains):
        raise s.SourceError('non-official source')
    body = (FIXTURES/URLS[url]).read_text()
    return s.FetchResult(url, url, s.page_title(body), body, None)


class JapaneseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads((ROOT/'session-time-sources.json').read_text())
        self.events = {e['id']:e for sid in ('sf','supergt') for e in json.loads((CALENDARS/(sid+'_2026.json')).read_text())}

    def source(self, url):
        return fetch(url, ['superformula.net','supergt.net'])

    def sf(self, event_id='sf-2026-r09', url='https://superformula.net/sf3/race/24431/'):
        return s.parse_superformula_schedule(self.source(url),self.events[event_id],2026,'Asia/Tokyo')

    def gt(self, event_id='supergt-2026-r01', url='https://supergt.net/races/round1-okayama-2'):
        return s.parse_supergt_schedule(self.source(url),self.events[event_id],2026,'Asia/Tokyo')

    def test_sf_live_fuji_six_sessions_and_exact_conversion(self):
        sessions = self.sf()
        self.assertEqual([x.name for x in sessions],['Free Practice 1','Free Practice 2','Qualifying 1','Race 1','Qualifying 2','Race 2'])
        self.assertEqual([s.editor_value(x,'Europe/Amsterdam')[1] for x in sessions],['04:00','07:45','02:40','08:00','03:00','08:00'])
        self.assertEqual(s.editor_value(sessions[3],'Europe/Amsterdam')[2],'2026-10-10T06:00:00Z')
        self.assertEqual(sessions[2].duration_minutes,45)

    def test_supergt_live_mobile_duplicates_supports_and_qualifying(self):
        sessions = self.gt()
        self.assertEqual(len(sessions),4)
        self.assertEqual([x.name for x in sessions],['Official Practice','Qualifying','Warmup','Race'])
        self.assertEqual([s.editor_value(x,'Europe/Amsterdam')[1] for x in sessions],['02:30','07:00','04:50','06:20'])
        self.assertEqual(sessions[1].duration_minutes,81)
        self.assertIsNone(sessions[-1].duration_minutes)

    def test_gt_layout_end_not_race_duration(self):
        sessions = self.gt('supergt-2026-r02','https://supergt.net/races/round2-fuji-2')
        self.assertEqual(len(sessions),4)
        self.assertEqual(sessions[-1].local_time,'14:00')
        self.assertIsNone(sessions[-1].duration_minutes)

    def test_sf_q3_and_reversed_practice_labels(self):
        sessions = self.sf('sf-2026-r08','https://superformula.net/sf3/race/24428/')
        self.assertEqual([x.name for x in sessions],['Free Practice 1','Qualifying','Free Practice 2','Race'])
        self.assertEqual(sessions[1].duration_minutes,62)

    def test_sf_triple_header_does_not_confuse_round_three(self):
        sessions = self.sf('sf-2026-r06','https://superformula.net/sf3/race/24425/')
        self.assertEqual([x.name for x in sessions if x.name.startswith('Race')],['Race 1','Race 2','Race 3'])
        self.assertEqual(len(sessions),7)

    def test_cancelled_or_relocated_race_cannot_be_reintroduced(self):
        with self.assertRaisesRegex(s.SourceError,'cancelled or relocated'):
            self.sf('sf-2026-r03','https://superformula.net/sf3/race/24419/')

    def test_unpublished_timetables_remain_empty(self):
        self.assertEqual(self.gt('supergt-2026-r07','https://supergt.net/races/round7-autopolis-2'),[])
        self.assertEqual(self.sf('sf-2026-r11','https://superformula.net/sf3/race/24434/'),[])

    def test_exact_round_discovery_repeated_circuits(self):
        for sid,calendar_url,ids,urls in [
            ('sf','https://superformula.net/sf3/race_taxonomy/2026/',['sf-2026-r06','sf-2026-r09'],['https://superformula.net/sf3/race/24425/','https://superformula.net/sf3/race/24431/']),
            ('supergt','https://supergt.net/calendar',['supergt-2026-r02','supergt-2026-r04'],['https://supergt.net/races/round2-fuji-2','https://supergt.net/races/round4-fuji-2'])]:
            for eid,url in zip(ids,urls):
                self.assertEqual(s.discover_japanese_event_url(self.source(calendar_url),self.events[eid],2026,sid),url)
            with self.assertRaises(s.SourceError):
                s.discover_japanese_event_url(self.source(calendar_url),self.events[ids[0]],2027,sid)

    def test_wrong_round_year_and_dates_rejected(self):
        src=self.source('https://superformula.net/sf3/race/24431/')
        with self.assertRaises(s.SourceError):s.parse_superformula_schedule(src,self.events['sf-2026-r06'],2026,'Asia/Tokyo')
        with self.assertRaises(s.SourceError):s.parse_superformula_schedule(src,self.events['sf-2026-r09'],2027,'Asia/Tokyo')
        event=json.loads(json.dumps(self.events['sf-2026-r09']))
        for x in event['sessions']:x['date']='2026-10-12'
        with self.assertRaises(s.SourceError):s.parse_superformula_schedule(src,event,2026,'Asia/Tokyo')
        with self.assertRaises(s.SourceError):self.gt('supergt-2026-r02')

    def test_incomplete_qualifying_and_conflicting_mobile_times_fail(self):
        source=self.source('https://superformula.net/sf3/race/24431/')
        bad=s.FetchResult(source.stable_url,source.final_url,source.title,source.body.replace('Rd.9 予選Q2','Unknown session'),None)
        with self.assertRaises(s.SourceError):s.parse_superformula_schedule(bad,self.events['sf-2026-r09'],2026,'Asia/Tokyo')
        source=self.source('https://supergt.net/races/round1-okayama-2')
        bad=s.FetchResult(source.stable_url,source.final_url,source.title,source.body.replace('data-from="09:30"','data-from="09:31"',1),None)
        with self.assertRaises(s.SourceError):s.parse_supergt_schedule(bad,self.events['supergt-2026-r01'],2026,'Asia/Tokyo')

    def test_dst_winter_summer_and_previous_day(self):
        for day,clock,expected in [('2026-10-10','15:00',('2026-10-10','08:00','2026-10-10T06:00:00Z')),('2026-11-21','15:00',('2026-11-21','07:00','2026-11-21T06:00:00Z')),('2026-11-21','00:30',('2026-11-20','16:30','2026-11-20T15:30:00Z')),('2026-03-29','09:30',('2026-03-29','01:30','2026-03-29T00:30:00Z')),('2026-03-29','10:30',('2026-03-29','03:30','2026-03-29T01:30:00Z'))]:
            self.assertEqual(s.editor_value(s.SourceSession('Race',day,clock,'Asia/Tokyo'),'Europe/Amsterdam'),expected)
        for sid in ('sf','supergt'):
            self.assertEqual(self.registry['editorTimeZones'][sid],'Europe/Amsterdam')
        self.assertEqual(s.resolve_event_timezone(self.registry,{'sourceTimeZone':'track'},{'circuitName':'Sepang'}),'Asia/Kuala_Lumpur')

    def test_end_to_end_proposals_unique_and_idempotent(self):
        with patch.object(s,'fetch_url',side_effect=fetch):
            result=s.scan(CALENDARS,self.registry,None,{'sf'},NOW,only_events={'sf-2026-r09'},replace_filled=True)
        proposals=result['proposals']
        self.assertEqual(len(proposals),6)
        self.assertTrue(all(p['status']=='open' and p['proposalType']=='time-update' for p in proposals))
        self.assertEqual(len({p['sessionId'] for p in proposals}),6)
        event=json.loads(json.dumps(self.events['sf-2026-r09']))
        for item in event['sessions']:
            item.update(next(p['proposed'] for p in proposals if p['sessionId']==item['id']))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sf_2026.json').write_text(json.dumps([event]))
            with patch.object(s,'fetch_url',side_effect=fetch):
                repeat=s.scan(root,self.registry,None,{'sf'},NOW,replace_filled=True)
        self.assertEqual(len(repeat['proposals']),6)
        self.assertTrue(all(p['status']=='verified' for p in repeat['proposals']))

    def test_manual_link_and_scheduled_refresh(self):
        event=json.loads(json.dumps(self.events['supergt-2026-r01']))
        event['officialScheduleUrl']='https://supergt.net/races/round1-okayama-2'
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'supergt_2026.json').write_text(json.dumps([event]))
            with patch.object(s,'fetch_url',side_effect=fetch):
                result=s.scan(root,self.registry,None,{'supergt'},NOW,only_events={event['id']},replace_filled=True)
            self.assertEqual(len(result['proposals']),4)
            self.assertTrue(all(p['status']=='verified' for p in result['proposals']))
            event.pop('officialScheduleUrl');(root/'supergt_2026.json').write_text(json.dumps([event]))
            self.assertEqual(len(s.calendar_inventory(root,{'supergt'},checked_at=datetime(2026,4,3,tzinfo=timezone.utc))),1)
            self.assertEqual(len(s.calendar_inventory(root,{'supergt'},checked_at=datetime(2026,5,3,tzinfo=timezone.utc))),0)

    def test_duration_only_change_is_actionable(self):
        with patch.object(s,'fetch_url',side_effect=fetch):
            result=s.scan(CALENDARS,self.registry,None,{'sf'},NOW,only_events={'sf-2026-r06'},replace_filled=True)
        correction=next(p for p in result['proposals'] if p['sessionName']=='Free Practice 2')
        self.assertEqual(correction['status'],'open')
        self.assertEqual(correction['proposalType'],'time-update')
        self.assertEqual(correction['current']['timeLocal'],correction['proposed']['timeLocal'])
        self.assertEqual(correction['proposed']['durationMinutes'],50)

    def test_shared_supergt_race_cannot_update_wrong_round(self):
        source=self.source('https://supergt.net/races/round1-okayama-2')
        body=source.body.replace('公式練習','決勝レース')
        changed=s.FetchResult(source.stable_url,source.final_url,source.title,body,None)
        with self.assertRaises(s.SourceError):
            s.parse_supergt_schedule(changed,self.events['supergt-2026-r01'],2026,'Asia/Tokyo')

    def test_invalid_clocks_fail_as_source_errors(self):
        for value in ('25:00','12:99','TBC','12:00-11:00'):
            with self.assertRaises(s.SourceError):s.japanese_clock_range(value)

if __name__ == '__main__':unittest.main()
