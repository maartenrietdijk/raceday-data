# Driver and team standings

`fetch_standings.py` saves both `drivers` and `teams` in the existing
`{series}_standings_{year}.json` document. Existing app readers can continue
using `drivers`. Team names are not abbreviated. `teamsUpdatedAt` records the
last successful team-data update, independently of the driver update.

The editor's **Klassement** view has **Rijders** and **Teams / constructors**
controls. **Rijders en teams ophalen** queues the GitHub workflow. Synchronize
when the workflow has finished to load the results. Both lists can be edited
and are included by **Publiceer klassement**. Existing local editor saves are
compatible; synchronize once to load newly fetched teams.

Install the changed files in the data repository, including the workflow at
`.github/workflows/fetch_standings.yml` (the root YAML is only a matching copy).
The workflow checks the local calendar every three hours on the default branch
once published. `auto_fetch_standings.py` only fetches a series within 18 hours
of a race or sprint race starting. There are six three-hour slots (0–3, 3–6,
6–9, 9–12, 12–15, 15–18 hours), with at most one attempt per slot and six attempts
per race. Failed attempts count too. Missed slots are not caught up. Unknown
start times, practice and qualifying do not trigger standings requests. The
window is measured from the start, including for endurance races.

WRC uses the start of the final scheduled special stage as one rally trigger.
Overlapping windows for a series share one fetch per workflow run. Time zones
match the app's calendar convention. Attempts persist in
`.raceday/auto-standings-state.json`, committed alongside successful updates.
GitHub schedules may run late, so fewer than six checks are possible. No
Motorsport.com requests are made when there is no eligible race.

Manual fetching from the editor (URL and series supplied) remains unrestricted.
An empty manual workflow dispatch uses the same race-window policy as the
schedule. `fetch_standings.py --all` remains an explicit maintenance command.

Examples:

```sh
python auto_fetch_standings.py --dry-run --year 2026
python auto_fetch_standings.py --year 2026
python fetch_standings.py --series f1 --url 'https://www.motorsport.com/f1/standings/2026/?type=Team&class='
python -m unittest discover -s tests -p '*standings.py'
node tests/test_editor_standings.cjs
```

The scraper preserves URL class filters. Scheduled imports use each series'
default class, as does the existing driver data model; they do not combine
separate class championships. Only advertised Teams tabs are fetched; a
manufacturer championship is not substituted for a team championship.
Unsupported or unreadable results never clear existing data. A failed team
fetch preserves its earlier standings and timestamp while allowing drivers
to refresh. Successful series are committed even if another series fails;
the workflow still reports that failure. Unchanged results do not create commits.

Live verification on 2026-09-28 fetched team standings for 15 series. ELMS
returned a table without ranked positions, so it was preserved and reported
as a failed import. Several other series advertise no Teams championship.

The editor's existing standings file selection remains on the 2026 season.
