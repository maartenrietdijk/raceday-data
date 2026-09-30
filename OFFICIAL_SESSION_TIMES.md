# Official session-time proposals

`official_schedule_scanner.py` checks official championship pages only and creates reviewable proposals. Scheduled scans target sessions whose `timeLocal` is `null`; for DTM and events with a manually configured source link they also recheck filled sessions during the 30 days before a race weekend and propose corrections when official times change. A manual event scan from the editor can propose corrections for sessions that already contain a time. The scanner never edits or publishes a calendar file itself.

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
python3 official_schedule_scanner.py --series dtm --include-filled --dry-run
python3 official_schedule_scanner.py --series gtwca_aus --event gtwca-aus-2026-r05 --replace-filled --dry-run
python3 official_schedule_scanner.py --fixtures-dir tests/fixtures/official --fixtures-only --now 2026-09-09T12:00:00Z
```

The result is `.raceday/session-time-proposals.json`. Repeated runs with unchanged official data retain one stable fingerprint. When an official time changes, the former proposal becomes `superseded` and the replacement points back to it.

## Review and publication

1. Synchronize the RaceDay editor with GitHub.
2. Open **Voorstellen**, or expand an event on its series page. The navigation badge counts `open` and `requires_review` items.
3. Compare official source time, IANA timezone, UTC instant, and the editor value.
4. Accept, reject, or undo. Conflicts and unresolved items cannot be accepted.
5. Use **Publiceer** on the proposal page. Only calendar files changed by accepted proposals plus the proposal decision file are uploaded. The normal publication step remains mandatory.

Accepted items disappear from the default **Openstaand** view immediately after acceptance. On load, the editor also reconciles proposals with the actual calendar: a proposal whose date and time are already present is treated as accepted, while an older proposal for the same session becomes superseded. A debug scan is intentionally separate: it can show `Komt overeen` or `Wijkt af` for sessions that already have a time, without making either result actionable.

On a series page, **Zoek officiële tijden** runs a targeted event scan with `--replace-filled`. The event stays open, shows the live GitHub Actions progress, loads the result automatically, and exposes accept/reject controls directly below that event. Returning to the separate proposals page is not required. Proposal rows and calendar sessions are ordered chronologically by date and time.

There is deliberately no global “accept all” action. Event-level acceptance includes only reliable `open` proposals for that event and rolls back atomically if one item fails.

## Timezone contract

The registry in `session-time-sources.json` mirrors the existing RaceDay app/editor contract:

- NASCAR Cup, O'Reilly, and Trucks: `America/New_York`
- F2, F3, NASCAR Euro, and every GT World Challenge series: `Europe/Amsterdam`
- Supercars: `Australia/Sydney` (matching the existing editor/calendar contract)

Official track/local time is first resolved in its IANA zone, converted to one UTC instant, and then converted to the editor zone. Fixed UTC offsets are never used. Non-existent DST times fail; ambiguous DST times become conflicts unless an official UTC/GMT column resolves the fold. Date changes caused by conversion are retained and highlighted in the editor.

## Source policy

`session-time-sources.json` is the maintained allowlist. Redirect targets must remain on an allowed official domain. Search snippets and secondary calendars are never parsed as evidence. Priority is event timetable or bulletin, event page, championship calendar, then official organizer/circuit page. The proposal store records both stable and final URLs, title, check time, source time/zone, editor time/zone, and source modification metadata when supplied.

For British GT, the scanner follows the official **Event Timetable PDF** from the event page and extracts only rows labelled `British GT Championship`; support-series rows are ignored. SRO event pages also prefer the linked official timetable PDF, including its fuller endurance schedule and date columns. The workflow installs the pinned `pypdf` reader for these steps.

Current series IDs in scope are `f2`, `f3`, `f1academy`, `formulae`, `nascar`, `nascar_oreilly`, `nascar_trucks`, `nascareuro`, `gtwce`, `gtwca_am`, `gtwca_asia`, `gtwca_aus`, `british_gt`, `dtm`, `wec`, `alms`, `lemanscup`, `indycar`, `indynxt`, `imsa`, and `supercars`. Formula E uses official round pages or official event previews and accepts only track-local times backed by explicit UTC evidence; rookie and fan-programme sessions are ignored. WEC, ALMS, Le Mans Cup, INDYCAR, INDY NXT, and IMSA use official schedule documents where available; the scanner prefers a linked official PDF over a timetable page. Supercars uses the official event track schedule and accepts only records whose series is exactly `Repco Supercars Championship`, excluding every support category and non-session activity. DTM uses the public official DTM event API because its event pages render the timetable only after JavaScript loads. Other series are skipped completely when none of their calendar files contains a TBC session, unless `--include-filled` is used for the proposals-page debug check.

## NASCAR scan window

Cup, O'Reilly, and Trucks are checked only for the current Monday–Sunday race week, starting Monday at 12:00 `Europe/Amsterdam` (including daylight-saving changes). Before noon on Monday, no NASCAR event is fetched. Events in later weeks, past weeks, or without a valid date are skipped before fetching; targeted and debug scans obey the same limit. A series with no race that week does not fall forward to its next event. Source sessions outside that week cannot generate time proposals. Skipped events remain unchanged/TBC, and the next proposal-store rebuild drops their old proposals using the existing merge behavior.

## Automation

`.github/workflows/official_session_times.yml` runs Monday at 11:17 UTC and Wednesday and Friday at 06:17 UTC and supports manual dispatch for one series or one exact event. Targeted editor scans set `replace_filled`, so official changes can replace already-filled sessions after review. It uses the repository's existing serialized write queue and commits only the proposal store. No secret other than GitHub's built-in repository token is required.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/proposal-logic.test.js
```

The HTML fixtures are small frozen extracts shaped like the official timetable tables, so tests never depend on live sites.

## Event links in the editor

The series view has an **Agenda / Klassement / Links** tab switcher. Each calendar event can store an `officialScheduleUrl`; the event ID and calendar file keep seasons separate. **Bewaar** saves a local draft; **Publiceer links** merges only these URLs into the current GitHub calendar and preserves all other remote fields. **Controleer** saves and publishes the link before starting a targeted scan. Clearing a published link restores automatic discovery. Unpublished drafts survive synchronizing the editor.

A manual link takes precedence over every automatic source, including feed-based discovery. Only HTTPS URLs on the series' registered official domains are accepted. DTM public event pages resolve to the API for that exact slug; MotoGP pages resolve using the exact event ID in the link. Unsupported JavaScript layouts, inaccessible pages, wrong dates, uncertain session matches and partially parsed schedules produce a visible warning in both Links and the event's Agenda view. The scanner does not silently fall back to a different source. Some formats still require a dedicated reader; an event link alone cannot make an unsupported page readable.

Events with manual links and filled times are rechecked during the 30 days before the weekend ends. NASCAR retains its existing current-race-week limit. The scheduled scanner continues to propose changes for review; it does not overwrite calendars. Manual scans read the dispatched GitHub branch so newly published links are used on that branch.

The series page displays the existing editor input timezone, its current clock, the Dutch clock and their current difference. IANA timezone rules account for daylight saving. The difference is explicitly current, since it may differ on the race date. The scanner registry now matches the existing America/New_York input contract for IndyCar and Indy NXT.

## Supercars event schedules

The reader decodes complete `raceSessionsCollection` records from the official Next.js stream, independent of field order. Only the Repco Supercars Championship category is accepted, including qualifying and TTSO (Top Ten Shootout). Offset-aware starts are converted through the track timezone to `Australia/Sydney`, which is the app calendar contract; the browser's My Time setting is irrelevant. Adelaide 26 November 2026 15:15 becomes 15:45 in the app calendar (04:45 UTC).

Races are numbered per weekend in proposals. Existing race slots are matched by date, not the official season race number. Discontinuous official race numbers require review and do not produce an actionable replacement. The original official name is retained as `sourceSessionName`. An exact registered Supercars event URL can include practice one day before an older calendar's first session; other date mismatches still fail validation.

Regression fixture: the official Adelaide schedule fetched on 30 September 2026. Run `python3 -m unittest discover -s tests -p 'test_supercars_schedule.py'`.
