import copy
import json
from pathlib import Path
import sys
import unittest
import subprocess
import tempfile
from unittest.mock import patch
import official_schedule_scanner as s
from bsb_fd_schedule import parse_bsb_schedule, parse_fd_schedule
ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures/bsb_fd'
CAL=ROOT/'tests/fixtures/calendars'
REG=json.loads((ROOT/'session-time-sources.json').read_text())

class BsbFdTests(unittest.TestCase):
    def events(self,series): return json.loads((CAL/f'{series}_2026.json').read_text())
    def cfg(self,series): return next(x for x in REG['series'] if x['seriesId']==series)
    def source(self,event):
        url=event['officialScheduleUrl'];series=event['seriesId']
        return s.FetchResult(url,url,'',(FIX/(series+'-'+url.split('/')[-1]+'.html')).read_text(),None)
    def calendar(self): return s.FetchResult('https://www.britishsuperbike.com/calendar','https://www.britishsuperbike.com/calendar','',(FIX/'bsb-calendar.html').read_text(),None)
    def parse(self,event,source=None):
        source=source or self.source(event)
        return parse_bsb_schedule(source,self.calendar(),event,2026) if event['seriesId']=='bsb' else parse_fd_schedule(source,event,2026,s.resolve_event_timezone(REG,self.cfg('fd'),event))
    def proposals(self,event,rows):
        return s.build_event_proposals(self.cfg(event['seriesId']),event['seriesId']+'_2026.json',event,self.source(event),rows,'UTC' if event['seriesId']=='fd' else 'Europe/London','2026-10-05T10:00:00Z',True,True)
    def test_all_eleven_bsb_rounds(self):
        for e in self.events('bsb'):
            with self.subTest(e=e['id']):
                rows=self.parse(e)
                self.assertEqual(len(rows),10)
                self.assertFalse(any('Gates' in r.name or 'Test' in r.name or 'Combined' in r.name for r in rows))
                ps=self.proposals(e,rows)
                self.assertFalse(any(p['proposalType']=='conflict' for p in ps))
                self.assertEqual(len({p['sessionId'] for p in ps if p['sessionId']}),len([p for p in ps if p['sessionId']]))
    def test_monday_and_cross_month_weekends(self):
        es=self.events('bsb')
        self.assertEqual(next(r.date for r in self.parse(es[0]) if r.name=='Race 3'),'2026-05-04')
        self.assertEqual(next(r.date for r in self.parse(es[5]) if r.name=='Race 3'),'2026-08-02')
        self.assertEqual(next(r.date for r in self.parse(es[7]) if r.name=='Race 3'),'2026-08-31')
    def test_assen_no_false_corrections(self):
        e=self.events('bsb')[8];rows=self.parse(e)
        self.assertTrue(all(r.timezone=='Europe/Amsterdam' for r in rows))
        self.assertTrue(all(p['status']=='verified' for p in self.proposals(e,rows)))
    def test_bsb_superpole_and_new_prequalifying(self):
        e=self.events('bsb')[7];ps=self.proposals(e,self.parse(e))
        p=next(p for p in ps if p['sessionName']=='Pre Qualifying')
        self.assertEqual(p['proposalType'],'new-session');self.assertEqual(p['proposed']['kind'],'qualifying')
        self.assertEqual(next(p['status'] for p in ps if p['sessionName']=='Superpole'),'verified')
    def test_bsb_wrong_round_year_title_rejected(self):
        e=self.events('bsb')[-1];src=self.source(e)
        for url,body in [(src.final_url.replace('r11','r5'),src.body),(src.final_url.replace('2026','2027'),src.body),(src.final_url,src.body.replace('R11 Brands Hatch','R5 Brands Hatch'))]:
            with self.subTest(url=url):
                with self.assertRaises(s.SourceError):self.parse(e,s.FetchResult(url,url,'',body,None))
    def test_bsb_tbc_and_contradictory_rows_rejected(self):
        e=self.events('bsb')[-1];src=self.source(e)
        with self.assertRaises(s.SourceError):self.parse(e,s.FetchResult(src.stable_url,src.final_url,'',src.body.replace('11:00 BST','TBC'),None))
        # Repeat the published timetable: never choose an arbitrary copy.
        with self.assertRaises(s.SourceError):self.parse(e,s.FetchResult(src.stable_url,src.final_url,'',src.body+src.body,None))
    def test_fd_last_event_exact_template_and_legacy_utc(self):
        e=self.events('fd')[-1];ps=self.proposals(e,self.parse(e))
        self.assertEqual([p['sessionName'] for p in ps],[x['name'] for x in e['sessions']])
        self.assertEqual([p['sessionId'] for p in ps],[x['id'] for x in e['sessions']])
        self.assertTrue(all(p['status']=='verified' for p in ps))
        self.assertEqual(ps[-1]['proposed']['utcInstant'],'2026-10-25T01:00:00Z')
        self.assertEqual(ps[2]['proposed']['kind'],'testing')
    def test_fd_other_readable_rounds_and_prospec_exclusion(self):
        for i in [0,3,4,5,6]:
            e=self.events('fd')[i]
            with self.subTest(e=e['id']):
                ps=self.proposals(e,self.parse(e))
                self.assertEqual(len(ps),len(e['sessions']))
                self.assertTrue(all(p['status']=='verified' for p in ps))
    def test_fd_missing_atlanta_and_orlando_am_pm_error_fail_closed(self):
        for i in [1,2]:
            with self.assertRaises(s.SourceError): self.parse(self.events('fd')[i])
    def test_fd_wrong_event_season_weekday_and_missing_range(self):
        e=self.events('fd')[-1];src=self.source(e)
        for url,body in [(src.final_url.replace('2026','2027'),src.body),(src.final_url,src.body.replace('SHORELINE SHOWDOWN','HIGH STAKES')),(src.final_url,src.body.replace('Friday, October 23','Sunday, October 23')),(src.final_url,src.body.replace('11:00AM - 12:30PM','TBC'))]:
            with self.assertRaises(s.SourceError): self.parse(e,s.FetchResult(url,url,'',body,None))
    def test_fd_duplicate_warmups_keep_ids_when_dates_shift(self):
        e=self.events('fd')[-1];rows=self.parse(e)
        e['sessions'][2]['date']='2026-10-24'
        ps=self.proposals(e,rows)
        self.assertEqual(ps[2]['sessionId'],'fd-2026-r08-s3')
        self.assertEqual(ps[4]['sessionId'],'fd-2026-r08-s5')
        e['sessions'].pop(4)
        self.assertTrue(any(p['status']=='requires_review' for p in self.proposals(e,rows)))
    def test_fd_cancelled_or_changed_kind_is_not_recreated(self):
        for change in ['cancelled','kind']:
            e=self.events('fd')[-1];rows=self.parse(e)
            if change=='cancelled': e['sessions'][-1]['name'] += ' (Cancelled)'
            else: e['sessions'][-1]['kind']='practice'
            ps=self.proposals(e,rows)
            self.assertEqual(ps[-1]['status'],'requires_review')
            self.assertIsNone(ps[-1]['proposed'])

    def test_cli_uncertain_source_does_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            (folder/'fd__fd-2026-r02.html').write_text((FIX/'fd-atlanta.html').read_text())
            (folder/'fd_2026.json').write_text(json.dumps(self.events('fd')))
            output=folder/'proposals.json'
            run=subprocess.run([sys.executable,str(ROOT/'official_schedule_scanner.py'),'--root',str(folder),'--registry',str(ROOT/'session-time-sources.json'),'--series','fd','--event','fd-2026-r02','--fixtures-dir',str(folder),'--fixtures-only','--output',str(output)],capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            result=json.loads(output.read_text())
            self.assertEqual(result['sourcesUsed'][0]['status'],'unresolved')

    def bsb_pdf(self):
        url='https://docs.msv.com/BSB%2026-11%20TS%20V1%20STD.pdf'
        return s.FetchResult(url,url,'Official timetable PDF',(FIX/'brands-hatch-2026-timetable.txt').read_text(),None)

    def test_bsb_pdf_confirms_current_brands_hatch_times(self):
        e=self.events('bsb')[-1]
        rows=s.parse_bsb_pdf(self.bsb_pdf(),self.calendar(),e,2026,e['officialScheduleUrl'])
        clocks={r.name:r.local_time for r in rows}
        self.assertEqual(clocks['Free Practice 3'],'10:10')
        self.assertEqual(clocks['Qualifying 1'],'12:00')
        self.assertEqual(clocks['Qualifying 2'],'12:22')
        self.assertEqual(clocks['Race 1'],'16:05')
        self.assertEqual(clocks['Warm Up'],'10:00')
        ps=s.build_event_proposals(self.cfg('bsb'),'bsb_2026.json',e,self.bsb_pdf(),rows,'Europe/London','2026-10-05T10:00:00Z',replace_filled=True)
        self.assertEqual(len(ps),10)
        self.assertTrue(all(p['status']=='verified' for p in ps))

    def test_pipeline_idempotent_and_no_calendar_mutation(self):
        events=[self.events('bsb')[-1]]+[self.events('fd')[i] for i in [0,3,4,5,6,7]]
        def fetch(url,domains):
            return self.calendar() if url.endswith('/calendar') else self.source(next(e for e in events if e['officialScheduleUrl']==url))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for series in ['bsb','fd']:
                (root/f'{series}_2026.json').write_text(json.dumps(self.events(series)))
            before=[(root/f'{series}_2026.json').read_bytes() for series in ['bsb','fd']]
            with patch.object(s,'fetch_url',side_effect=fetch), patch.object(s,'fetch_pdf_url',return_value=self.bsb_pdf()):
                a=s.scan(root,REG,None,{'bsb','fd'},'2026-10-05T10:00:00Z',only_events={e['id'] for e in events},include_filled=True)
                b=s.scan(root,REG,None,{'bsb','fd'},'2026-10-05T10:00:00Z',only_events={e['id'] for e in events},include_filled=True)
            self.assertEqual(a,b)
            self.assertTrue(all(x['status']=='ok' for x in a['sourcesUsed']))
            self.assertEqual(len(a['proposals']),45)
            self.assertFalse(any(p['status'] in {'requires_review','unresolved'} for p in a['proposals']))
            self.assertEqual(before,[(root/f'{series}_2026.json').read_bytes() for series in ['bsb','fd']])

if __name__=='__main__': unittest.main()
