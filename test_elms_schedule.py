import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import official_schedule_scanner as s

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/elms'
CAL=ROOT/'tests/fixtures/calendars'
URLS=json.loads((FIX/'sources.json').read_text())
REG=json.loads((ROOT/'session-time-sources.json').read_text())
CFG=next(x for x in REG['series'] if x['seriesId']=='elms')
EVENTS=json.loads((CAL/'elms_2026.json').read_text())
DOCS=['barcelona','2330','2375','2358','2403','2491']

class ELMSScheduleTests(unittest.TestCase):
    def source(self,index=0):
        body=(FIX/(DOCS[index]+'.txt')).read_text()
        return s.FetchResult('', '', 'Official timetable PDF',body,None)

    def parse(self,index=0,source=None,event=None):
        event=event or EVENTS[index]
        return s.parse_elms_timetable(source or self.source(index),event,2026,s.resolve_event_timezone(REG,CFG,event))

    def test_all_six_real_timetables_have_four_existing_sessions(self):
        expected=[['11:50','10:10','15:05','12:00'],['11:50','10:10','15:10','12:00'],['10:50','10:50','14:55','13:00'],['11:00','09:00','12:30','13:00'],['11:55','09:55','14:55','12:00'],['11:00','10:15','15:15','14:30']]
        for index,event in enumerate(EVENTS):
            with self.subTest(event=event['id']):
                rows=self.parse(index)
                self.assertEqual([x.name for x in rows],['Free Practice 1','Free Practice 2','Qualifying','Race'])
                self.assertEqual([x.local_time for x in rows],expected[index])
                self.assertEqual([x.duration_minutes for x in rows],[90,90,[90,81,90,81,84,90][index],240])
                proposals=s.build_event_proposals(CFG,'elms_2026.json',event,self.source(index),rows,'Europe/Amsterdam','2026-10-05T12:00:00Z',replace_filled=True)
                self.assertTrue(all(x['sessionId'] in {slot['id'] for slot in event['sessions']} for x in proposals))
                self.assertFalse(any(x['proposalType']=='new-session' for x in proposals))

    def test_actual_pdf_extraction_preserves_rows(self):
        try:import pypdf
        except ImportError:self.skipTest('PDF dependency is installed in the scan workflow')
        for index,doc in enumerate(DOCS):
            body=s.extract_pdf_text((FIX/(doc+'.pdf')).read_bytes(),preserve_layout=True)
            actual=self.parse(index,s.FetchResult('','','Official timetable PDF',body,None))
            self.assertEqual(actual,self.parse(index))

    def test_portimao_and_silverstone_convert_to_editor_zone(self):
        self.assertEqual(s.editor_value(self.parse(5)[-1],'Europe/Amsterdam'),('2026-10-10','15:30','2026-10-10T13:30:00Z'))
        self.assertEqual(s.editor_value(self.parse(4)[-1],'Europe/Amsterdam')[1],'13:00')

    def test_incomplete_wrong_year_wrong_event_require_review(self):
        source=self.source()
        for body in [source.body.replace('QUALIFYING SESSION - LMGT3','UNPUBLISHED SESSION - LMGT3'),source.body.replace('2026 EUROPEAN','2025 EUROPEAN'),source.body.replace('4 HOURS OF BARCELONA','4 HOURS OF IMOLA'),source.body.replace('FREE PRACTICE 1','UNPUBLISHED PRACTICE 1')]:
            with self.assertRaises(s.SourceError):self.parse(source=s.FetchResult('','','',body,None))
        with self.assertRaises(s.SourceError):self.parse(event=EVENTS[2])

    def test_support_and_admin_rows_never_become_sessions(self):
        source=self.source();body=source.body.replace('Michelin Le Mans Cup','Another Support Series').replace('Ligier European Series','Another Support Series')
        self.assertEqual(self.parse(source=s.FetchResult('','','',body,None)),self.parse())

    def test_missing_start_end_and_cancelled_rows_fail(self):
        source=self.source()
        for body in [source.body.replace('11:50','--:--'),source.body.replace('FREE PRACTICE 1','FREE PRACTICE 1 (TBC)'),source.body.replace('16:35','15:00')]:
            with self.assertRaises(s.SourceError):self.parse(source=s.FetchResult('','','',body,None))

    def test_newest_timetable_version_is_selected_numerically(self):
        source=s.FetchResult('','https://www.europeanlemansseries.com/en/race/test','','<a href="/en/race/document/download/9999">Timetable V3</a><a href="/en/race/document/download/2">Timetable V10</a>',None)
        self.assertTrue(s.discover_elms_timetable_pdf(source).endswith('/2'))

    def test_full_scan_fills_tbc_slots_without_editing_calendar(self):
        event=copy.deepcopy(EVENTS[5]);event.pop('officialScheduleUrl')
        for slot in event['sessions']:slot['timeLocal']=None
        def fetch(url,domains,extra_headers=None):
            self.assertTrue(s.allowed_url(url,domains));body=(FIX/URLS[url]).read_text()
            return s.FetchResult(url,url,s.page_title(body),body,None)
        def pdf(url,domains,preserve_layout=False):
            self.assertTrue(preserve_layout);self.assertTrue(s.allowed_url(url,domains))
            return s.FetchResult(url,url,'Official timetable PDF',(FIX/URLS[url]).read_text(),None)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'elms_2026.json';path.write_text(json.dumps([event]));before=path.read_bytes()
            with patch.object(s,'fetch_url',side_effect=fetch),patch.object(s,'fetch_pdf_url',side_effect=pdf):
                result=s.scan(root,REG,None,{'elms'},'2026-10-05T12:00:00Z',only_events={event['id']})
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(result['proposals']),4)
            self.assertTrue(all(x['status']=='open' and x['proposalType']=='time-update' for x in result['proposals']))
            self.assertEqual(result['sourcesUsed'][0]['status'],'ok')

    def test_unpublished_2027_stays_tbc(self):
        event=json.loads((CAL/'elms_2027.json').read_text())[0]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'elms_2027.json';path.write_text(json.dumps([event]));before=path.read_bytes()
            with patch.object(s,'fetch_url',return_value=s.FetchResult('','https://www.europeanlemansseries.com/en/race/4-hours-of-barcelona-2027','2027 event','<p>Coming soon</p>',None)):
                result=s.scan(root,REG,None,{'elms'},'2026-10-05T12:00:00Z',only_events={event['id']})
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(any(x['status']=='open' for x in result['proposals']))
            self.assertEqual(result['sourcesUsed'][0]['status'],'unresolved')
