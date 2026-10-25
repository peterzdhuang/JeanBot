# LeetCode-only Discord tracker

Status: implemented in the local code, with offline verification and a checked-in Top 300 catalog. The running bot/database have not been changed; live PostgreSQL/Discord checks remain pending. See README.md for migration, backup, rollback, and deployment instructions.

## Goal

Simplify the bot to LeetCode tracking: one daily Medium quest from a Top 300 interview-question list, submission links, daily/weekly progress, optional reminder pings, and one combined daily report after the final participant deadline. Preserve all existing LeetCode history and timezone behavior.

## Scope and product decisions
- Keep joining/leaving, personal timezones, the 3:15 AM challenge-day boundary, submissions, history, streaks, difficulty totals, daily participation, and tracking leaderboards.
- Remove Jeancoins, shops, purchases, inventories, equipment, pets, feeding, dungeons, combat, vibe checks, rotating game advertisements, and related buttons/messages. Remove XP/levels and game badges from the active experience; preserve existing values in a migration archive rather than destroying old records.
- Offer exactly one quest per guild and challenge-date label. It must be Medium, or an Easy problem with an explicitly documented constraint assessed as Medium-level practice. Preserve the actual LeetCode difficulty and display the required constraint. Remove difficulty selection from `/quest` and all multiple-quest boards.
- Keep logging other LeetCode solves, including Easy and Hard. A valid solve still satisfies daily participation and protects the existing streak; completing the assigned Medium is tracked separately. This preserves the current tracking rule while simplifying quests.
- Show both week-to-date and month-to-date progress after each successful submission.
- Keep reminders enabled by default for new members, preserve existing preferences, and provide an immediate personal opt-out.
- Replace timezone-by-timezone wrap-ups with one combined report for each challenge-date label after the last applicable deadline.

## Preserve history before changing anything

The current PostgreSQL schema stores player snapshots with lifetime counters and at most 30 days of daily counts/quest bits. It explicitly rejects submission history. `tracker.py` prunes older daily buckets, and legacy JSON import discards problem identifiers. Existing Discord cards can contain problem links, but `/submit` currently normalizes submission URLs to problem URLs. The README also describes older quest behavior that differs from the current tracker implementation; use the actual data and code when migrating.

Required preservation:

1. Back up PostgreSQL and every available legacy data file before migration. Keep source files unchanged and verify that the backup can be restored.
2. Preserve every available LeetCode record, raw legacy entry, lifetime/difficulty total, daily count, streak/best streak, quest claim, enrollment/leave marker, timezone, reminder preference, guild setting, and delivery marker. Include inactive members.
3. Import detailed records from available legacy files without dropping URLs, slugs, submission IDs, original date labels, or timestamps. Retain raw source records for ambiguous conversions. Reconcile overlap with PostgreSQL instead of adding the same historical counts twice.
4. Preserve historical challenge-date assignments. Do not relabel old activity using a member's current timezone or the migration date.
5. Stop deleting LeetCode history after 30 days. A rolling query/cache is acceptable; pruning the underlying records is not.
6. Keep aggregate-only history explicitly labeled as such. Missing submission links, individual problems, or already-pruned dates cannot be reconstructed from counters. Do not fabricate records or reset totals to match the smaller detailed dataset.
7. Preserve old quest claims with their original meaning. Do not reinterpret historical Easy/Hard claims as completion of the new Medium quest.
8. Archive removed game fields intact for rollback; removing commands must not remove the player rows holding tracking data.

Migration must be versioned, transactional, repeatable without duplication, and fail safely on ambiguous overlap. Produce a reconciliation summary comparing players, totals, daily buckets, detailed records, timezone settings, and reminder preferences before/after. Rollback must restore the old schema/data with the compatible old bot version.

## Timezones and challenge days

- Retain each member's existing IANA timezone and the existing PST/PDT/PT, MST/MDT/MT, CST/CDT/CT, UTC/GMT aliases.
- A challenge labeled date D runs from D at 03:15 local time, inclusive, until D+1 at 03:15, exclusive. A 02:00 solve belongs to the previous calendar date. A solve exactly at the cutoff belongs to the next challenge day.
- Compute local wall-clock boundaries with timezone-aware datetimes. Preserve DST behavior: a day can last 23 or 25 hours.
- Keep existing timezone-change and leave/rejoin rules unless changed in a separate request. Leaving/rejoining must never delete history, reset lifetime totals, or permit a duplicate daily quest claim.
- Persist the timezone and UTC deadline used for each participant/day. Later timezone changes must not rewrite past reports or move an already-open deadline.
- `/setup` changes the default for future enrollments; existing players keep their timezone.
- `/timezone` schedules a change effective at the member's current deadline, preserving the open day, streak, and history; jumps that would skip a challenge date are refused. Leaving before it takes effect creates no new enrollment period or reporting obligation; rejoining with an explicit timezone discards the pending change.

## One daily Medium quest

### Top 300 catalog

Use a versioned, checked-in catalog representing the intended Top 300 interview questions. Each entry includes LeetCode problem ID, slug, title, difficulty, canonical URL, and source/rank metadata. Record the source URL, retrieval date, catalog version, and checksum.

Selected source: [dev-sujan/leetcode-dsa, Top 300 Most Asked LeetCode Questions](https://github.com/dev-sujan/leetcode-dsa/blob/47213b2882e800002a2f0d6fa43dd1c83a2b8895/top_300_questions.md), pinned to commit `47213b2882e800002a2f0d6fa43dd1c83a2b8895`. This is a community-curated collection, not an official LeetCode frequency ranking. Upstream advertises 300 but contains 355 rows / 343 distinct problems. The checked-in snapshot keeps the first 300 unique problems in source topic order; ranks record that order, not verified interview frequency. The selection policy and upstream counts are stored in `top300.json`.

Validate 300 distinct source entries, then filter to publicly accessible Medium questions and explicitly reviewed Easy variants. The shipped pool contains **183 Medium questions and one constrained Easy variant**. Every eligible entry was checked against its official LeetCode page. Count Complete Tree Nodes is corrected from the source's Easy to LeetCode's current Medium; Encode and Decode Strings is excluded as Premium-only. Startup and quest requests use the local snapshot.

The Easy exception is Backspace String Compare with LeetCode's linear-time/constant-space follow-up: O(n + m) time, O(1) auxiliary space, no edited-string buffers or stacks. Its Medium-level practice assessment is editorial judgment; LeetCode still labels the base problem Easy. Persist the constraint, rationale, and official source URL with the quest. Plain Easy and all Hard questions are excluded. Members self-attest to satisfying the constraint when submitting; the bot does not verify their code.

### Assignment

- Assign one problem per `(guild_id, challenge_date)` from that Medium-level pool. Members viewing the same date label receive the same problem, even when their deadlines differ.
- Persist the selected problem and catalog version. Restarts, repeated requests, and catalog refreshes must not change an assigned quest.
- Use a deterministic shuffled cycle and exhaust the eligible pool before repeating a problem. Avoid repeating the previous cycle's final problem at the next cycle's start when possible.
- Never fall back to Easy/Hard or the unrelated general catalog. If no eligible pool exists, show an actionable configuration error; ordinary tracking stays available.
- `/quest` shows title, actual difficulty (or Easy + Medium-level constraint), mandatory constraint when applicable, problem link, challenge date, personal deadline, completion status, and how to submit the attempt link. Reminder/report/submission displays carry the same constraint.
- One matching submission completes the quest once per member/day. Additional submissions never add extra quest completions.
- Keep historical quest versions. Activate the new rules on a recorded challenge date; do not replace existing assignments or claims in an already-open day.

## Submission links and progress

`/submit` accepts an actual attempt URL, such as `https://leetcode.com/problems/<slug>/submissions/<id>/`, and preserves that attempt link separately from the canonical problem URL. Support LeetCode's `/submissions/detail/<id>/` format when the problem can be resolved; otherwise request a problem URL as an additional field rather than guessing the slug. Problem-only links should prompt the member to supply an attempt link without incrementing counts.

- Validate HTTPS, an exact allowed LeetCode hostname, supported path, and numeric submission ID. Do not accept lookalike hosts. Strip harmless tracking parameters when normalizing links.
- Persist guild/member IDs, problem identity/title/difficulty, submission ID and URL, received-at UTC timestamp, assigned challenge date, timezone/deadline snapshot, and whether the submission completed the quest.
- Preserve the existing trusted-submission model: a member attests that the solve was accepted. Do not claim that acceptance or ownership was verified. No LeetCode login credentials are required.
- Attribute new submissions using the time the bot receives them, matching the existing workflow. Do not automatically backdate a late `/submit` from an attempt URL.
- Reject an already-recorded submission ID for the same member/guild, including after a restart. Repeated attempts for the same problem/member/challenge-day must not inflate daily solve counts. The same problem may count again on a later day; historical totals retain their existing semantics.
- Commit the record, aggregates, and quest completion atomically. Discord response failures must not cause a second count when retried.

The public confirmation contains:

1. Member, problem title, difficulty, and clickable **View submission** attempt link.
2. Challenge date, timezone, and deadline; whether this completed today's quest.
3. Current and best streak, plus daily solve count.
4. **This week:** solves, participation days / elapsed eligible days, and quest completions / assigned eligible quests.
5. **This month:** the same metrics, plus lifetime solves.

Example: `This week: 5 solves · 4/5 days submitted · 3/5 quests completed.`

Use the member's challenge-date labels: weeks start Monday and months start on the first. Include the current day in progress through today; do not count it as missed until its deadline closes. Denominators exclude future days, days before enrollment, and inactive periods. Exclude pre-launch days from the new quest denominator. Clearly label partial historical coverage instead of treating missing old daily buckets as zero activity. Count daily participation separately from the number of solves.

`/history` paginates all available records with attempt links where known and aggregate-only rows where details are unavailable. `/progress period:week|month` exposes the same calculations independently of submitting.

## Opt-out reminders

Retain the existing minute-based background scheduler as the cron-equivalent. Run one scheduler owner; do not also configure an independent cron process that sends duplicate pings.

- The inherited server slots default to **20:00** and **02:15**, evaluated in each member's local timezone for their open challenge day. Existing members keep the inherited schedule; new members default to a single personal 20:00 reminder with an optional 02:15 `late_reminder`. A personal schedule overrides the inherited server slots. `/snooze` pauses pings for 1–1440 minutes without moving the deadline.
- Ping only active, opted-in members who have not submitted any qualifying solve for that day. Include the quest link, personal deadline, submission instructions, and `/reminders enabled:false`.
- `/reminders enabled:false` immediately stops personal reminder pings; `true` resumes them. Preserve opt-out across restarts, migration, and rejoin. Display the setting in `/status` and onboarding.
- Keep server-wide enable/disable and configurable inherited reminder slots through admin-only `/reminder_settings`. Personal opt-out and personal reminder times always take precedence over inherited slots.
- Recheck preferences and completion immediately before sending. Batch mentions safely and allow mentions only for the intended recipients.
- Persist delivery state keyed by guild/member/challenge-date/slot. Retry transient failures with bounded backoff and respect Discord rate limits.
- Preserve the one-hour catch-up window for missed slots; skip stale reminders after that and all reminders after the personal deadline. Skip nonexistent DST slots and send repeated-hour slots at most once.
- Reminders do not generate extra quests or alter completion/history. Reminder opt-out does not remove a member from tracking or reports; reports list users without notifying them.

## One report at the last end time

For a challenge label D, each eligible participant has their own D+1 03:15 local deadline. The report becomes due at the **maximum UTC deadline** among that day's participants. Publish it on the first successful scheduler tick afterward, normally within one minute.

- Persist a participant/day roster and their deadlines, using existing enrollment semantics: include members enrolled during that challenge day and record when they leave. Members who leave before their personal cutoff are excused from that day's missed list; members who leave after cutoff retain their closed result. Rejoining cannot rewrite a closed result.
- Freeze each result at its personal cutoff. Newer-day submissions must never clear an earlier missed day.
- Compute the final report deadline from the stored roster/deadlines, including excused members, so joins, leaves, or timezone changes cannot move a previously scheduled report earlier. New eligible participants can extend it before it is published.
- Send one logical report per guild/date in the configured report channel. Remove the existing per-timezone wrap-ups.
- Show challenge date, quest title/link, final closing timestamp, submitted/missed/excused totals, and the full lists of submitted and missed members. Submitted rows show solve count, quest status, and attempt link(s) where known. A member who solved a different problem appears as submitted with the quest marked incomplete.
- Include only eligible participants, with explicit excused members if any. Keep everyone in the output using continuation messages/pages when Discord limits require it; do not truncate names silently.
- Avoid all reminder mentions in reports. A day with no eligible participants needs no report.
- Store a report delivery record and Discord message IDs. Use a durable outbox with a stable report identifier, retry failures, and reconcile an uncertain send before retrying where possible. Discord send and database commit are not atomic; document the remaining rare duplicate-delivery window.
- After downtime, deliver unsent reports oldest first with a delayed label and rate limiting. Do not drop historical reports or mark them sent before successful delivery.

Example: if D's participant deadlines are 03:15 America/New_York and 03:15 America/Vancouver on D+1, the combined report waits for Vancouver's cutoff. Compute using actual offsets for that date, including DST.

## Commands and interface

Keep `/setup`, `/join`, `/leave`, `/submit`, `/status`, `/today`, `/history`, `/quest`, `/leaderboard`, `/reminders`, `/reminder_settings`, and `/storage`; add `/progress`, `/goal` (weekly participation goal, 3–7 days, default 4), `/snooze`, `/timezone`, and `/buddy` (mutual-consent study buddies).

- `/today` shows each active member's open challenge date, submitted/pending status, quest completion, and personal deadline. Different timezones may currently have different date labels.
- `/leaderboard` uses current streak, then fewer missed eligible days, then lifetime solves as tie breakers; remove XP ranking.
- `/status` presents tracking totals, streaks, timezone, cutoff, reminder preference, and current quest status.
- Remove obsolete slash commands during Discord command synchronization. Replace persistent game buttons with working tracking actions and clean up onboarding, presence, descriptions, and README examples.
- Admin setup/reminder/storage commands retain Manage Server authorization. Keep member data scoped to its guild.

## Storage and implementation approach

Continue using PostgreSQL. Add normalized tables for submissions, daily quests, participant/day snapshots and completion summaries, enrollment/timezone changes going forward, catalog versions, and reminder/report delivery records. Use database uniqueness constraints for submissions, member/day/problem counts, quests, and scheduler event keys. Serialize conflicting member/day writes transactionally.

Retain imported lifetime baselines separately from detailed new records so aggregate-only history can coexist without double counting. Store unavailable historical metadata as unknown. A finite history cache can remain for performance, but the persisted source of truth must retain all LeetCode records.

The current 16 KB player snapshot restriction and prohibition on `submissions` require an explicit schema migration; putting growing history into the old JSONB snapshot will not work. Remove tracking dependencies on `game.py` without losing archived fields. Revisit the current 45 MiB new-player guard and documented 50 MB budget: indefinite history requires storage monitoring and adequate capacity, never silent pruning. If writes cannot persist, reject the submission clearly rather than reporting success.

## Delivery plan

1. Inventory and back up available PostgreSQL/legacy data; document recoverable history and coverage gaps. Use the documented pinned Top 300 source.
2. Build and validate the versioned catalog. Add the new schema and non-destructive migration, reconciliation, and rollback path.
3. Implement single-Medium quest assignment and durable submissions; retain existing tracking/timezone semantics and add weekly/monthly progress/history queries.
4. Remove game commands, rewards, UI, and runtime dependencies. Synchronize Discord commands and update documentation.
5. Adapt the scheduler for opted-out reminders and final-deadline reports, with durable rosters and delivery state.
6. Validate migration on a restored copy and run targeted automated tests. Smoke-test Discord commands, links, mentions, scheduler delivery, and PostgreSQL transactions before replacing the running bot.

## Acceptance checks

- Before/after reconciliation accounts for every available historical LeetCode record and aggregate, including inactive members, old Easy/Hard solves, quest claims, timezones, and reminder opt-outs. Re-running migration creates no duplicates.
- Restarting, crossing 30 days, leaving/rejoining, and deploying a new catalog never deletes history or changes an assigned quest.
- Every new daily quest is a public Medium or an Easy with a documented Medium-level constraint from the pinned Top 300 snapshot; plain Easy, Hard, and Premium-only questions cannot be selected. Only one exists per guild/date, shared across timezone groups. Constraint text appears throughout the quest flow; submitted difficulty remains trusted.
- Actual submission links remain clickable in confirmations and history. Invalid/problem-only URLs do not count; retrying the same attempt or another same-day attempt for the same problem does not inflate totals.
- Weekly/monthly figures cover Monday/week, month/year boundaries, leap days, enrollment/inactive periods, incomplete legacy coverage, and daily participation versus solve counts.
- Tests cover submissions immediately before/at/after 03:15, DST changes, preserved timezone aliases, and immutable historical date assignments.
- An opted-out or already-submitted member receives no reminder. Restart, duplicate scheduler ticks, DST folds, and catch-up processing do not intentionally resend a successful slot.
- With multiple timezones, no combined report appears before the final stored deadline; exactly one logical report lists every submitted/missed eligible member after closure, with pagination when necessary.
- Reports remain correct after a member leaves or changes timezone; next-day solves do not clear yesterday's miss. Downtime catches up without dropping reports.
- Removed game commands/buttons/rewards are absent, while tracking commands and archived source data remain usable. PostgreSQL/Discord failures do not create false success or lose accepted records.
