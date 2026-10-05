import unittest
from pathlib import Path
import official_schedule_scanner as scanner

class Formula1ScheduleTests(unittest.TestCase):
    def fetch(self, name, transform=lambda body: body):
        body = (Path(__file__).parent / "fixtures" / "formula1" / (name + ".html")).read_text()
        return scanner.FetchResult("https://www.formula1.com", "https://www.formula1.com", name, transform(body), None)

    def test_bahrain_tbc_numeric_placeholders_are_ignored(self):
        self.assertEqual(scanner.parse_formula1_schedule(self.fetch("bahrain")), [])

    def test_saudi_tbc_numeric_placeholders_are_ignored(self):
        self.assertEqual(scanner.parse_formula1_schedule(self.fetch("saudi")), [])

    def test_published_austin_sessions_and_dst_conversion(self):
        sessions = scanner.parse_formula1_schedule(self.fetch("austin"))
        self.assertEqual(len(sessions), 5)
        self.assertEqual(sessions[0].local_time, "17:30")
        self.assertEqual(sessions[-1].local_time, "20:00")
        from datetime import datetime
        from zoneinfo import ZoneInfo
        def amsterdam(item):
            return datetime.fromisoformat(item.date + "T" + item.local_time).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Amsterdam")).strftime("%H:%M")
        self.assertEqual(amsterdam(sessions[0]), "19:30")
        self.assertEqual(amsterdam(sessions[-1]), "21:00")

    def test_visible_tbc_overrules_internal_numbers(self):
        sessions = scanner.parse_formula1_schedule(self.fetch("austin", lambda b: b.replace("<time>17:30</time>", "TBC")))
        self.assertEqual(len(sessions), 3)

    def test_session_status_tbc_overrules_visible_clocks(self):
        body = self.fetch("austin").body
        needle = '\\"description\\":\\"Practice 1\\",'
        self.assertIn(needle, body)
        body = body.replace(needle, needle + '\\"sessionStatus\\":\\"TBC\\",')
        fetch = self.fetch("austin", lambda _: body)
        self.assertEqual(len(scanner.parse_formula1_schedule(fetch)), 4)

    def test_confirmation_marker_is_only_added_to_f1_source(self):
        source = self.fetch("austin")
        for kind, expected in [("formula1-jsonld", True), ("f1academy-schedule", False)]:
            proposal = scanner.proposal_base({"seriesId": "f1", "name": "F1", "sourceKind": kind}, "f1_2026.json", {"id": "event"}, source, "2026-10-03T12:00:00Z")
            self.assertEqual("confirmationPolicy" in proposal["source"], expected)

    def test_wrong_offset_cannot_be_accepted(self):
        self.assertEqual(scanner.parse_formula1_schedule(self.fetch("austin", lambda b: b.replace("-05:00", "-04:00"))), [])

    def test_missing_public_timetable_fails_closed(self):
        import re
        self.assertEqual(scanner.parse_formula1_schedule(self.fetch("austin", lambda b: re.sub(r"<time.*?</time>", "", b))), [])

if __name__ == "__main__":
    unittest.main()
