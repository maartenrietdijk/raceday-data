import unittest
from pathlib import Path
import official_schedule_scanner as s
FIX=Path(__file__).parent/'fixtures/indycar'
class IndycarHtmlTests(unittest.TestCase):
    def source(self,year):
        url=f'https://www.indycar.com/Schedule/{year}/St-Petersburg'
        return s.FetchResult(url,url,'',(FIX/f'st-petersburg-{year}.html').read_text(),None)
    def test_published_html_times_and_eastern_zone(self):
        rows=s.parse_indycar_html(self.source(2026),{},2026)
        self.assertEqual([(x.name,x.local_time) for x in rows],[('Practice 1','13:30'),('Practice 2','09:30'),('Qualifying','16:30'),('Warmup','09:00'),('Race','12:00')])
        self.assertTrue(all(x.timezone=='America/New_York' for x in rows))
    def test_unpublished_times_remain_empty(self):
        self.assertEqual(s.parse_indycar_html(self.source(2027),{},2027),[])
    def test_wrong_season_is_rejected(self):
        with self.assertRaises(s.SourceError):s.parse_indycar_html(self.source(2026),{},2027)
    def test_unclear_timezone_is_rejected(self):
        x=self.source(2026)
        with self.assertRaises(s.SourceError):s.parse_indycar_html(s.FetchResult(x.stable_url,x.final_url,'',x.body.replace('1:30PM ET','1:30PM'),None),{},2026)
    def test_2027_calendar_has_unique_rounds_and_no_invented_times(self):
        import json
        data=json.loads((Path(__file__).resolve().parents[1]/'indycar_2027.json').read_text())
        self.assertEqual(len(data),17)
        self.assertEqual([e['roundNumber'] for e in data],list(range(1,18)))
        ids=[x['id'] for e in data for x in e['sessions']]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all(x['timeLocal'] is None for e in data for x in e['sessions']))
        self.assertEqual([x['date'] for e in data for x in e['sessions'] if x['name'].startswith('Race')][14:16],['2027-08-28','2027-08-29'])
        self.assertTrue(all('2027/' in e['officialScheduleUrl'] for e in data))
