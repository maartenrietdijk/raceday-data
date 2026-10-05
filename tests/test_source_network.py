import unittest
from email.message import Message
from http.client import InvalidURL, RemoteDisconnected
from unittest.mock import MagicMock, patch
import official_schedule_scanner as s

class SourceNetworkTests(unittest.TestCase):
    def test_official_event_url_spaces_and_unicode_are_encoded_once(self):
        response=MagicMock()
        response.geturl.return_value='https://www.intercontinentalgtchallenge.com/event/153/Indianapolis%208%20Hour'
        response.read.return_value=b'<title>Indianapolis</title>'
        response.headers=Message()
        response.__enter__.return_value=response
        with patch.object(s,'urlopen',return_value=response) as opening:
            result=s.fetch_url('https://www.intercontinentalgtchallenge.com/event/153/Indianapolis 8 Hour',['www.intercontinentalgtchallenge.com'])
            self.assertEqual(opening.call_args.args[0].full_url,response.geturl.return_value)
            self.assertEqual(result.title,'Indianapolis')
            s.fetch_url('https://www.intercontinentalgtchallenge.com/event/150/Nürburgring%20Race',['www.intercontinentalgtchallenge.com'])
            self.assertIn('N%C3%BCrburgring%20Race',opening.call_args.args[0].full_url)
            self.assertNotIn('%2520',opening.call_args.args[0].full_url)

    def test_transport_errors_become_source_errors(self):
        for reader in (s.fetch_url,s.fetch_pdf_url):
            for error in (TimeoutError('timeout'),RemoteDisconnected('closed'),InvalidURL('bad path')):
                with self.subTest(reader=reader.__name__,error=type(error).__name__):
                    with patch.object(s,'urlopen',side_effect=error):
                        with self.assertRaises(s.SourceError):
                            reader('https://example.test/event',['example.test'])
