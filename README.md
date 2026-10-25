# LeetCode tracker

A Discord bot for LeetCode submissions, personal deadlines, streaks, daily activity squares, one daily Medium-level quest, opt-out reminders, and a combined daily report. PostgreSQL retains tracking history. Shops, pets, dungeons, XP, and currency are removed from the bot experience.

See [spec.md](spec.md) for requirements and implementation decisions.

## Run

Use Python 3.10+ and PostgreSQL. Set `DISCORD_TOKEN` and `DATABASE_URL` in `.env` beside `bot.py` or in the environment. Environment variables take precedence. Keep credentials and backups private.

```sh
python3 -m pip install -r requirements.txt
python3 bot.py
```

Run one bot process. The minute-based scheduler supplies the cron-equivalent reminder/report jobs; no second cron sender is needed. The bot needs View Channel, Send Messages, Embed Links, and Attach Files. Read Message History lets report retries reconcile uncertain deliveries. Manage Server is required for administrative commands; Administrator and Message Content intent are unnecessary.

```text
/setup channel:#leetcode timezone_name:America/Vancouver
/join timezone_name:PST
/quest
/submit url:https://leetcode.com/problems/add-two-numbers/submissions/12345/
/status
/progress period:month
/reminders enabled:false
```

For a `https://leetcode.com/submissions/detail/12345/` attempt, also provide `problem_url:https://leetcode.com/problems/add-two-numbers/`. Problem-page URLs alone do not count. Optional `difficulty:Easy|Medium|Hard` is trusted, including when it differs from local metadata. Omitted difficulty uses local metadata or Unknown. The bot does not verify acceptance or ownership and does not require LeetCode credentials.

Successful submission cards link to the actual attempt and show today's solve count, current/best streak, weekly/monthly totals, and a 12-week GitHub-style grid. Known zero days, green activity, unknown history, inactive days, and future days have different colors. Rendering failure falls back to text while retaining the saved submission. Duplicate attempts and repeated same-day problems do not add counts, including across restarts.

## History and upgrades

**Existing history is preserved.** Schema v2 upgrades transactionally to v3. Before changing the version marker, the bot copies original metadata, guild rows, and player rows into `lc_tracker_backups` and checks that the copies match. A failed check cancels migration. Original game/unknown fields stay archived in player snapshots.

Growing daily records and attempt details are stored separately in `lc_tracker_history`; quests, report rosters, and delivery state use `lc_tracker_events`. Nothing prunes LeetCode records after 30 days. Existing lifetime and difficulty totals are carried forward rather than rebuilt from incomplete detail.

The old bot retained only 30 days of daily counts and did not save submission links. This upgrade cannot recover discarded records from counters. Available legacy JSON files are archived intact, and new imports retain original date labels and raw entries. Unknown historical dates are labeled unknown. `/history` includes detailed links where available and aggregate-only daily rows where details are missing.

Before deploying, stop the old process, keep the old code, and take a provider snapshot or full PostgreSQL dump. Back up legacy data files separately. Retained history and immutable migration copies require additional database capacity. `/storage` reports usage; the bot does not delete history to fit a quota.

The storage utility initializes/migrates without Discord and can export all bot tables to a new private file:

```sh
python3 storage.py --backup lcbot-backup.json
python3 storage.py --import-json data.json
```

`--backup` opens/migrates first, so take the provider snapshot **before** running it for a pre-upgrade backup. Exports use mode 0600 and never overwrite an existing file. Keep backups outside publicly uploaded application archives.

Legacy imports never edit/delete their source file. They refuse to overwrite overlapping PostgreSQL members. Even when overlap prevents importing, the raw source is archived for reconciliation. A previously imported file does not add its totals again. Reconcile overlapping old detail before adding it to active history.

For rollback, stop the new bot, export complete v3 data to preserve new submissions, and restore the pre-deployment provider snapshot/dump into a separate database. Compare players, lifetime/difficulty totals, daily counts, timezones, and opt-outs against the export and immutable `v2_meta`, `v2_guild`, and `v2_player` backups. Run matching old code against that restored database after accounting for submissions since upgrade. Keep the v3 database/export intact. Changing only the schema marker is not a rollback.

An additional rollback export is available when an original v2 migration backup exists:

```sh
python3 storage.py --backup complete-v3-before-rollback.json
python3 storage.py --export-v2 original-v2-restore.sql
psql -v ON_ERROR_STOP=1 --dbname=SEPARATE_EMPTY_DATABASE --file=original-v2-restore.sql
```

The SQL export creates the original three tables in the separate database and restores the verified original v2 rows. It refuses to overwrite an existing file, uses mode 0600, and aborts if any target table already has records. Generating the script changes no database records beyond normal storage initialization. The script restores the **pre-migration baseline only**: later submissions and new members remain in the v3 database/export and need reconciliation before switching back to old code. Keep that v3 database intact; do not point this restore at it. The full provider backup remains the preferred complete rollback source.

## Daily quests and the Top 300 source

Exactly one Medium-level problem is assigned per guild/challenge-date label. Members on the same date get the same quest across timezones. Assignments persist across restarts and catalog changes. A stable shuffled cycle exhausts the eligible pool before repeating. Any logged solve protects the participation streak; matching the assigned problem separately completes the quest, regardless of declared difficulty.

The checked-in [top300.json](top300.json) uses the community-curated [Top 300 Most Asked LeetCode Questions](https://github.com/dev-sujan/leetcode-dsa/blob/47213b2882e800002a2f0d6fa43dd1c83a2b8895/top_300_questions.md), pinned to its source commit. This is a curated practice list, not an official frequency ranking. Upstream contains 355 rows / 343 unique problems despite its title; the snapshot takes the first 300 unique problems in source topic order, with the policy recorded in metadata.

The eligible pool is **184 quests: 183 public Mediums plus one constrained Easy**. Every eligible entry was checked against its official LeetCode page. The source's outdated Count Complete Tree Nodes difficulty is corrected to Medium, and Premium-only questions are excluded.

The Easy exception is [Backspace String Compare](https://leetcode.com/problems/backspace-string-compare/), requiring its official follow-up: linear time and O(1) auxiliary space, with no stacks or edited-string buffers. The bot labels this **Easy + Medium-level constraint** and shows the requirement in quests, reminders, daily reports, and matching submission cards. Medium-level practice is our assessment, not an official LeetCode reclassification. Submission means the member attests to meeting the constraint; the bot does not inspect their code. Plain Easy questions are never selected.

The default catalog works out of the box. To prepare a replacement, use a JSON array of exactly 300 source entries in ranked order:

```json
[
  {
    "id": 2,
    "slug": "add-two-numbers",
    "title": "Add Two Numbers",
    "difficulty": "Medium",
    "publicly_available": true
  }
]
```

This illustrates one entry, not a complete catalog. Confirm the source and metadata for all 300 entries, then build a validated snapshot:

```sh
python3 quest_catalog.py source-questions.json --source-url https://your-source.example/list --output top300.json
```

The builder records ranks, canonical URLs, retrieval date, version, and checksum. Only publicly available Medium entries or explicitly reviewed Easy variants become quests, so the pool can contain fewer than 300 entries. An Easy variant additionally requires `practice_difficulty:"Medium"`, `constraint`, `constraint_reason`, and `constraint_source_url` citing its official LeetCode page. Set `TOP300_CATALOG` for another snapshot path. No network request is needed at startup or when submitting. Previously assigned quests and all player history remain intact when the catalog changes.

## Personal challenge days

A day runs from **03:15 to the next 03:15 in the member's timezone**. At 02:00, submissions count toward the previous date; the exact cutoff starts the new day. PST/PDT/PT map to America/Los_Angeles; MST/MDT/MT to America/Denver; CST/CDT/CT to America/Chicago; EST/EDT/ET to America/New_York. Use America/Phoenix for Arizona, UTC, or another IANA region. Aliases follow DST, and days can last 23 or 25 hours.

`/timezone timezone_name` schedules a timezone change for the end of the current challenge day: today keeps its existing deadline, and the streak, history, and lifetime totals carry over. A jump that would skip a challenge date is refused; change through an intermediate timezone instead. A member who leaves before the change takes effect stays inactive with no new reporting obligation, and rejoining with an explicit timezone discards the pending change. Equivalent deadlines can change without a reset. Old submissions retain their original challenge-date labels. Reports snapshot deadlines so changes do not rewrite past results.

Weeks start Monday; months start on the first, using challenge-date labels. Progress separates solves, participation days, and assigned quest completions. Future/inactive days do not inflate denominators. Partial historical coverage is labeled. An open pending day does not break a streak before its cutoff.

## Reminders and daily reports

Reminders go only to active, opted-in members without a solve that day. New members get one **20:00 local** reminder by default; `/reminders reminder_time:19:30 late_reminder:true` sets a personal time and adds an optional 02:15 final nudge. Members from before this upgrade keep the inherited server schedule (default **20:00 and 02:15 locally**) until they choose a personal time. A personal schedule overrides the inherited server slots. `/reminders enabled:false` opts out immediately and persists across restarts/rejoin; `true` resumes reminders. `/snooze minutes:60` (1–1440) pauses pings for that long without changing the deadline. Admin `/reminder_settings` sets the inherited server slots and the master switch, which disables all reminders. Existing preferences survive migration.

`/goal days:4` sets a weekly participation goal of 3–7 days (default 4); progress cards show days met against the goal. `/buddy action:invite|accept|remove|status member:@friend` pairs members only by mutual consent, and either side can remove the connection.

Reminders catch up for at most an hour; nonexistent DST slots are skipped and repeated slots are marked once. They never ping everyone or roles. Submission and preference state are rechecked before sending.

For date D, one combined report waits until the latest stored D+1 03:15 deadline among that day's participants. Each result freezes at its personal cutoff. Reports list submitted, missed, and excused members, solve counts, quest status, and available attempt links without notifying listed members. Large reports use continuation messages.

The scheduler retains participant rosters and delivery markers and catches up delayed reports oldest first. Successful pages record Discord message IDs so retries resume. Discord delivery and database commits are separate: an uncertain send can produce a rare duplicate when message-history reconciliation is unavailable. Failures are retried rather than marking unsent reports delivered.

## Commands

- Tracking: `/join`, `/leave`, `/submit`, `/status [member]`, `/today`, `/leaderboard`.
- History/progress: `/history [member] [page]`, `/progress period:week|month`, `/quest`.
- Preferences: `/reminders [enabled] [reminder_time] [late_reminder]`, `/snooze [minutes]`, `/goal [days]`, `/timezone timezone_name`.
- Social: `/buddy action [member]`.
- Admin: `/setup channel [timezone_name]`, `/reminder_settings`, `/storage`.

Game commands are removed during slash-command synchronization. Legacy game modules/data remain inert for compatibility; tracking awards no currency, XP, pet growth, or dungeon progress.

## Verification

```sh
python3 -m unittest -v
python3 -m py_compile bot.py tracker.py storage.py quest_catalog.py activity_grid.py
```

Offline tests cover migration backups/reconciliation and rollback on failure, historical retention, durable duplicates, trusted difficulty, cutoff/DST, progress/grid output, quest cycles, reminders, and report deadlines/delivery. Storage uses a transactional fake and Discord transport is mocked. A migration trial on a restored PostgreSQL copy and a Discord smoke test are still required before deploying.

The implementation workspace cannot resolve the dependency download host, so dependency installation and live Discord/PostgreSQL verification must run on the deployment host. No production database or Discord server was modified during implementation.

## Retention review and synthetic probes

[retention_review.md](retention_review.md) is the **historical baseline** review, written before weekly goals, personal reminder timing, snooze, `/timezone`, and buddies were added. It covers executed behavioral/scaling probes, comparisons with habit-building apps, prioritized recommendations, and a prospective retention measurement plan. Its original evidence is preserved unchanged in [retention_probe_results_baseline.json](retention_probe_results_baseline.json); [retention_probe_results.json](retention_probe_results.json) is the latest re-run against the current code. These are code observations, not actual user-retention rates; this review changes no runtime behavior or history.

```sh
python3 retention_probes.py --output retention_probe_results.json
```
