# Official session-time proposals

`official_schedule_scanner.py` checks official championship pages only and creates reviewable proposals for sessions whose `timeLocal` is `null`. It never edits or publishes a calendar file.

## Run manually

From the repository root:

```bash
python3 official_schedule_scanner.py
```

Useful safe variants:

```bash
python3 official_schedule_scanner.py --dry-run
python3 official_schedule_scanner.py --series gtwca_aus --verbose
python3 official_schedule_scanner.py --series f2 --event f2-2026-r01
python3 official_schedule_scanner.py --fixtures-dir tests/fixtures/official --fixtures-only --now 2026-09-09T12:00:00Z
```

The result is `.raceday/session-time-proposals.json`. Repeated runs with unchanged official data retain one stable fingerprint. When an official time changes, the former proposal becomes `superseded` and the replacement points back to it.

## Review and publication

1. Synchronize the RaceDay editor with GitHub.
2. Open **Voorstellen**. The navigation badge counts `open` and `requires_review` items.
3. Compare official source time, IANA timezone, UTC instant, and the editor value.
4. Accept, reject, or undo. Conflicts and unresolved items cannot be accepted.
5. Use **Publiceer** on the proposal page. Only calendar files changed by accepted proposals plus the proposal decision file are uploaded. The normal publication step remains mandatory.

There is deliberately no global “accept all” action. Event-level acceptance includes only reliable `open` proposals for that event and rolls back atomically if one item fails.

## Timezone contract

The registry in `session-time-sources.json` mirrors the existing RaceDay app/editor contract:

- NASCAR Cup, O'Reilly, and Trucks: `America/New_York`
- F2, F3, NASCAR Euro, and every GT World Challenge series: `Europe/Amsterdam`

Official track/local time is first resolved in its IANA zone, converted to one UTC instant, and then converted to the editor zone. Fixed UTC offsets are never used. Non-existent DST times fail; ambiguous DST times become conflicts unless an official UTC/GMT column resolves the fold. Date changes caused by conversion are retained and highlighted in the editor.

## Source policy

`session-time-sources.json` is the maintained allowlist. Redirect targets must remain on an allowed official domain. Search snippets and secondary calendars are never parsed as evidence. Priority is event timetable or bulletin, event page, championship calendar, then official organizer/circuit page. The proposal store records both stable and final URLs, title, check time, source time/zone, editor time/zone, and source modification metadata when supplied.

Current series IDs in scope are `f2`, `f3`, `f1academy`, `nascar`, `nascar_oreilly`, `nascar_trucks`, `nascareuro`, `gtwce`, `gtwca_am`, `gtwca_asia`, `gtwca_aus`, `british_gt`, and `dtm`. A series is skipped completely when none of its calendar files contains a TBC session.

## Automation

`.github/workflows/official_session_times.yml` runs Monday, Wednesday, and Friday at 06:17 UTC and supports manual dispatch for one series or one exact event. It uses the repository's existing serialized write queue and commits only the proposal store. No secret other than GitHub's built-in repository token is required.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/proposal-logic.test.js
```

The HTML fixtures are small frozen extracts shaped like the official timetable tables, so tests never depend on live sites.
