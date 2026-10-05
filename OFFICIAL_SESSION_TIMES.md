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

Supercars proposal titles use weekend session numbering: Practice, Qualifying, Top Ten Shootout and Race each have their own sequence. Sponsor prefixes and season race references remain only in `sourceSessionName`. The editor updates names when accepting time corrections. Event acceptance saves once after every proposal succeeds, and restores calendar, decisions, undo data and dirty state if any proposal fails. New session IDs are stable source-derived IDs; older proposals with IDs occupied by unrelated draft sessions receive a free ID without replacing the draft.


## 24H Series (added 5 October 2026)

`24hseries` now participates in scheduled and editor-triggered session-time scans.
The dedicated reader uses the event page's **Time Schedule** section and its explicit
`data-date`, `data-time` and `data-tz` attributes. Countdown metadata, results, track
days, grid activities and finish rows are excluded. An explicit `Time TBC` label
always overrides a numeric placeholder. Event title/year and track timezone must
match; conflicting or missing evidence does not create a time proposal.

The new `24hseries_2027.json` contains Kyalami plus the five European events:
Mugello, Spa-Francorchamps, Red Bull Ring, Paul Ricard and Barcelona. Round numbers
1–6 are chronological within this combined app calendar. European session days and
durations are provisional templates from 2026; every start time remains `null`.
Red Bull Ring uses the 2026 Nürburgring 12-hour split-race template. The common
single-block Qualifying format is used, including Mugello, pending its timetable.
Kyalami's published practice, qualifying and night practice are converted from
Africa/Johannesburg to Europe/Amsterdam. Its race remains TBC because the publisher
explicitly marks the start time unconfirmed.

For a generic Qualifying row, all published class/driver runs form one block from
the first start to the last finish (including gaps). An incomplete/TBC block is
never partially filled. Legacy numbered slots without class labels are ambiguous
and require review rather than attaching the wrong class. No support-session
results are used as timetable evidence.

All five 2026 events and all six 2027 events have explicit officialScheduleUrl
values. The fetched 2026 pages currently show archived results without the Time
Schedule section; these correctly yield no time proposals. Published 2027
European event pages likewise currently have no timetable.

Verification: compact actual page extracts for all eleven events; tests for title,
year, timezone, unpublished schedules, TBC race placeholders, support exclusion,
qualifying aggregation, existing-session updates and split-race numbering. Live
scanner checks passed for Kyalami and Mugello on 5 October 2026. Run:

```sh
python3 -m unittest discover -s tests -p 'test_24hseries_schedule.py' -v
```

Official calendar: https://www.24hseries.com/races
Reference timetable: https://www.24hseries.com/races/michelin-24h-kyalami-2027


## ELMS (added 5 October 2026)

`elms` now supports both scheduled scans and the editor's official-time controls.
The registered European Le Mans Series event URL points to the same ACO/LMEM
website family as WEC, ALMS and Le Mans Cup. A dedicated timetable reader is used
because ordinary PDF text extraction separates the ELMS columns. The existing
PDF downloader now has an optional layout-preserving mode; other series retain
their existing extraction behavior.

The scanner selects the highest numbered **Timetable Vn** linked by the event
page, validates the document's event/year and dates, and reads only ELMS rows.
Bronze tests, promoter tests, support championships, inspection laps, grid/green
flag times and administrative activities are excluded. Free Practice 1 and 2,
all four class qualifying runs as one block, and the actual race are matched to
the existing four calendar slots. Qualifying includes gaps from the first start
to the last finish. Missing or repeated classes, overlapping qualifying runs,
unconfirmed rows and incomplete schedules require review.

Session durations are authoritative for ELMS, so a changed duration can produce
a proposal even if its start time is unchanged. Track time is converted through
UTC to Europe/Amsterdam. This matters at Silverstone and Portimão: the latest
Portimão 2026 timetable's Saturday 14:30 track-time race start becomes 15:30 in
the editor (13:30 UTC).

All six verified 2026 event links have been added to `elms_2026.json`. Three
legacy Le Castellet session kinds have been corrected from race to practice /
qualifying. Existing calendar times and results are preserved. Future event
URLs are constructed from the event name and calendar year; unpublished 2027
event pages/time schedules yield an unresolved source status and remain TBC.

Verification: six original 2026 PDF fixtures plus layout text and event-page
extracts. Tests cover PDF extraction, numeric timetable version selection,
support filtering, wrong event/year, incomplete/TBC schedules, timezone
conversion, matching existing IDs, scan behavior and editor acceptance/undo.
Live Portimão scan passed on 5 October 2026. The full suite has 73 Python tests
and three JavaScript suites, all passing.

```sh
python3 -m unittest discover -s tests -p 'test_elms_schedule.py' -v
```

Sources:
- https://www.europeanlemansseries.com/en/race/4-hours-of-barcelona-2026
- https://www.europeanlemansseries.com/en/race/4-hours-of-portimao-2026
- https://www.europeanlemansseries.com/en/race/document/download/2491


### BSB and Formula DRIFT

`bsb-timetable` reads the BSB event page and verifies its season, exact round,
circuit and weekday dates against the official season calendar. Only BSB's
own practice, pre-qualifying, qualifying, Superpole, warmup and three races
are accepted. Gates, tests and combined practice standings are excluded.
Calendar times remain Europe/London, including Assen converted from
Europe/Amsterdam (the site calls summer venue clocks CET).

`fd-timetable` reads the dated PRO timetable on each Formula DRIFT event page.
PROSPEC, public opening hours, ceremonies and other fan activities are excluded;
a combined opening ceremony + Top 16 row retains its published block start.
Practice 1/2 and repeated Warmup slots stay separate, with Warmup as `testing`
and Top 32/16 as `race`. Real published start/end ranges supply duration.
2026 retains the existing UTC calendar convention; new seasons use the app's
America/Los_Angeles convention, with venue IANA zones for the original times.
The editor handles legacy `timeUTC` baselines and removes that old field when
an accepted time correction is stored under `timeLocal`.

All 19 existing 2026 events now have their verified official event links.
Filled upcoming BSB/FD events are refreshed alongside TBC slots. Calendars are
never edited by the scanner; corrections remain proposals for review.
Snapshots of all 19 official event pages support regression tests. As checked
on 5 October 2026, Atlanta has no readable schedule and Orlando's practice
range is 3:00PM–4:30AM. These events remain unresolved instead of guessing an
end time or silently treating PROSPEC sessions as PRO. The reader deliberately
rejects incomplete, contradictory, wrong-event and wrong-season timetables.
