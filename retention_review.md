# Retention and consistency review

Evaluated October 25, 2026. Runtime behavior and stored user history were not changed by this review.

The bot has a useful foundation: a shared daily problem, personal deadlines, durable submissions, activity squares, opt-out reminders, and visible progress. Its main weaknesses are reminder noise, fragile streak motivation, and a daily success threshold that gives no credit for meaningful unsuccessful practice. Improve the habit loop before adding more features.

These are code and synthetic-user findings, not measured churn. There is no production export in this workspace, and these probes did not connect to Discord/PostgreSQL or read credentials. Actual retention percentages, notification fatigue, and causal effects on users cannot be inferred from passing tests or synthetic personas.

## Executed probes

Run the complete suite with:

```sh
python3 retention_probes.py --output retention_probe_results.json
python3 -m unittest
```

The [JSON evidence](retention_probe_results.json) records inputs and observations. The executable probes use actual tracker functions and bot callbacks with in-memory transport/storage fakes. Existing automated tests also pass: **96 tests**.

| Probe | Demonstrated behavior | Retention implication to investigate |
| --- | --- | --- |
| First join | Confirms timezone/deadline and opt-out privately; a Daily quest button exists, but the welcome card has no problem link. | Put the first useful action directly in onboarding; measure first-submission conversion. |
| Submission formats | A problem-specific attempt works. A global `/submissions/detail/` link requires a second problem URL. A problem-only URL is rejected. | Help people copy the correct link; retain actual attempt links and duplicate protection. |
| Missed-day comeback | A seven-day streak becomes zero after one missed day, then one on return. Best streak, all eight records, grid, and weekly progress survive. | Make return feel worthwhile by highlighting weekly consistency and a welcome-back message. |
| Timezone correction | Changing a deadline timezone requires leave/rejoin; a seven-day current streak becomes one, while history and best streak remain. | Travel/settings changes can feel punitive. Offer a future-effective timezone change that preserves closed records. |
| Flexible schedule | Four practice days in a full week display as 4/7. No configurable weekly goal exists. | Members aiming for four days cannot see that they met their own target. |
| Partial old history | Six unknown grid days plus one observed solve still display as 1/7, with a partial-history warning. | The warning is present, but the headline denominator can imply six failures. Show known coverage separately. |
| Paused member | No reminders after leaving; the open leave day is excused in the report, but weekly progress still counts it in the denominator. | Progress and reports disagree about excused days. This is a concrete consistency issue. |
| Repeat practice | Another same-day attempt for a counted problem is rejected and omitted from history; the same problem can count again on another day. | Keep solve counts deduplicated, while considering a separate attempt/review diary. |
| Reminder volume | 100 pending members in one timezone produce 100 messages at 20:00 and another 100 at 02:15. A repeated tick adds zero duplicates. | **200 channel messages/day**, with two intended reminders per pending member. Batch delivery and offer personal scheduling. |
| Reminder controls | Three opted-out and one submitted member in a ten-member fixture receive zero reminders; the other six receive one each. There is no snooze button or personal quiet-hours setting. | Opt-out works. Add useful intermediate choices so muting forever is not the easiest alternative. |
| Daily report | Submitted/missed names are visible in the configured channel; notification mentions are disabled. | This is the requested accountability feature. Whether its visibility helps or deters a given group needs user feedback. |
| Submission failure | A failed database commit does not count a solve; a Discord response failure after commit leaves the solve saved, and a retry cannot count it twice. | Reliable feedback and history protection are strengths to preserve. |
| Delivery uncertainty | Injecting a reminder send followed by a failed commit produces a second ping on retry. A similar report failure is recovered using message-history reconciliation. | Give reminders stable delivery markers and recovery logic as well. |

### Storage/scheduler load

The scheduler opens a guild edit for every member, including members with no due reminder. Each edit hydrates the full guild's retained history. A single preference edit also reads all members' history. This makes a quiet scan grow roughly with **members × total guild history**, plus serialization and roster scans.

| Synthetic guild | Quiet-tick queries | Transactions | Serialized returned payload | Sends / writes |
| --- | ---: | ---: | ---: | ---: |
| 5 members × 30 history days | 44 | 8 | 0.841 MiB | 0 / 0 |
| 20 members × 90 history days | 134 | 23 | 26.627 MiB | 0 / 0 |
| 30 members × 180 history days | 194 | 33 | 113.488 MiB | 0 / 0 |

These payload sizes are summed JSON serializations returned by the fake, not measured PostgreSQL network traffic. Wall-clock timings in the JSON include the fake's full-table copies and transaction snapshots; they are **not production latency estimates**. Query counts and redundant hydration paths identify the scaling risk without establishing a real response-time SLA.

Prefilter due members from one snapshot before opening their write transaction; recheck eligibility under the lock before delivery. Next, query reminder eligibility and member/day summaries directly, and load detailed history only for the requested member/page/range. Keep all historical rows. Do not solve this by pruning history. Avoid holding the guild transaction through slow Discord network work when a durable outbox can coordinate delivery safely.

## What other apps offer

These mechanisms are examples to adapt and test. Their presence in another product does not prove an improvement for a LeetCode Discord group.

| App | Verified mechanism | Adaptation for this bot |
| --- | --- | --- |
| Duolingo | Friend Streaks require the other person's acceptance and shared daily participation. Its published 22% higher daily-lesson completion figure is a reported association, not a causal lift forecast for this bot. | An optional accountability partner and a shared weekly commitment, with invitations accepted by both people and limited nudges. [Official source](https://blog.duolingo.com/friend-streak/) |
| Todoist | Daily/weekly goals, chosen days off, and vacation mode protect the ongoing routine during planned breaks. | Choose 3–7 practice days/week; show progress against that target and record planned rest explicitly. Keep raw solve streak/history truthful. [Official source](https://www.todoist.com/help/todoist/features/introduction-to-karma-OgWkWy) |
| Strava | User-selected weekly/monthly/annual goals, suggested goals, and progress attached to completed activities. | After each submission, show `3/4 practice days this week` and the next small milestone, alongside the existing grid. [Official source](https://support.strava.com/en-us/articles/15401694-goals-on-the-strava-app) |
| Anki | Review scheduling and desired recall retention are balanced against review workload. This concerns memory retention, which is different from users returning to an app. | Offer spaced review of previously attempted problems and a manageable workload; evaluate learning separately from visits. [Official manual](https://docs.ankiweb.net/deck-options.html) |
| Beeminder | Planned breaks adjust required activity during the break. | Add a dated pause/resume schedule rather than requiring a user to leave tracking. Avoid importing its monetary commitment mechanics. [Official source](https://help.beeminder.com/article/98-can-i-put-my-goal-on-pause-for-a-little-bit) |

Duolingo also describes testing habit-oriented reminder/onboarding language and encouraging the first seven-day routine. That supports testing a clear initial commitment, rather than assuming every additional notification is useful. [Official product account](https://blog.duolingo.com/putting-in-work-the-habit-of-language-learning/)

## Recommended implementation order

### 1. Fix reliability and misleading feedback

- Batch reminders by compatible local deadline/quest and mention only due, opted-in members. For the 100-member fixture, batches of 50 would reduce the two daily bursts from 200 messages to four while retaining two intended reminders per pending member.
- Prefilter reminder eligibility and add scoped storage reads. Persist reminder delivery intent/markers and reconcile uncertain sends; preserve per-member opt-out and cutoff checks immediately before sending.
- Change progress to expose known observed days, unavailable history, and excused days separately. A partially observed week should say `1 day recorded; 6 days unavailable`, not present an apparent 1/7 failure score.
- Add a future-effective timezone setting: freeze the current challenge deadline, activate the new zone at the next valid boundary, and retain historical labels, membership periods, and honest streak semantics. Explicitly avoid overlapping/double-credit days.

These are the highest-confidence improvements because the probes establish the underlying behavior. Their effect on retention still needs measurement.

### 2. Let members choose a sustainable commitment

- Keep the one shared daily quest. During onboarding, choose a personal weekly target, such as four days, and a preferred local reminder time. Members wanting daily practice can choose seven.
- Make the join response useful immediately: today's problem link, deadline, how to obtain an attempt URL, and a short example submission. The existing quest button can remain.
- Add **Snooze**, **Done today**, and **Reminder settings** actions. “Done today” should open submission entry and save a real attempt link; clicking it alone must not claim a solve. A personal reminder outside late-night quiet hours should be the default proposed experience; a 02:15 final nudge becomes an explicit choice.
- Show the goal on every successful submission: `3/4 days this week · 1 more to reach your target`. Preserve the current/best solve streak and lifetime total as additional context.

### 3. Make a missed day survivable

- On return, show `Welcome back—your 42 solves are still here` and a small next action, while preserving the actual current streak and grid.
- Offer scheduled rest/pause days with explicit labels. Keep solve days, planned rest, and practice/check-in days distinct; never fabricate accepted solves or recolor missed historical days as solved.
- Consider a separate short practice check-in for a genuine attempt or review, including a problem link and a brief takeaway. It can count toward a **practice** target while only a submitted solve counts toward **solve** totals/streaks and quest completion.
- Preserve the requested submitted/missed report. Use neutral wording, publish it in the agreed accountability channel, and celebrate returning members. Any visibility preference should be an explicit product decision, not a silent removal of the report.

### 4. Add accountability and a path through the material

- Try optional buddy pairs with a shared weekly participation target. Require mutual acceptance; avoid automatic messages to other members or repeated friend nudges.
- Add a visible path through interview patterns or a weekly topic theme while retaining one daily guild quest. A shuffled pool alone provides variety without showing what the member is learning next.
- Use optional, time-limited review suggestions for previous mistakes. Same-day review records can be retained separately without incrementing unique daily problem counts.

Leave pets, currencies, shops, and unrelated mini-games out of these changes, consistent with the LeetCode-only scope.

## Measure actual retention before claiming success

Current persistence can support **solve activity** for periods with reliable history. It does not record sessions, command views, link clicks, or when a person read a reminder. A sent Discord message is not proof of an impression. Older v2 snapshots retained only 30 daily buckets, and prior rejoins could overwrite the original join date. A first retained solve is not necessarily a person's first-ever solve.

For prospective measurement, add immutable `first_joined_at`, `first_solve_at`, cohort provenance (`new` versus `migrated`), source/version, experiment exposure, and explicit reminder/preference events. A rejoin must not replace original cohort fields. Keep this operational data local to the existing backend; a third-party analytics SDK is unnecessary for the initial pilot.

| Metric | Definition |
| --- | --- |
| First-solve activation | New enrollments with a successful saved submission within 24 hours of immutable first join, divided by all fully observed new enrollments. Requires new timestamp instrumentation. |
| D7 / D28 solve retention | At least one saved solve in elapsed-UTC windows `[join+7d, join+8d)` / `[join+28d, join+29d)`. Only include cohorts whose full window has closed. These are solve-retention metrics, not app-return metrics. |
| Week-two / week-four return | At least one solve in `[join+7d, join+14d)` / `[join+21d, join+28d)`. Useful alongside exact-day metrics for non-daily schedules. |
| Weekly consistency | Distinct solve days in a fully observed local challenge week, plus practice-target attainment if that feature is introduced. Show numerator and denominator. |
| Comeback after a miss | Members returning with a solve within seven days of their first missed required day, among members with a fully observed seven-day follow-up. A planned rest day is not a miss. |
| Reminder guardrails | Sent reminders/user/week, duplicate sends, opt-out rate, failure rate, and solve-after-reminder timing. Temporal proximity alone does not prove the reminder caused a solve. |
| Learning guardrails | Solve days, quest completions, and later review recall; distinguish practice check-ins from solved problems. |

Use one documented elapsed-time definition for retention and local 03:15 challenge dates for habit progress. They differ across DST/timezone changes and must not be mixed. Keep leavers in their original retention cohort denominator. Exclude or separately label cohorts with unknown historical coverage; do not selectively include old positive records while dropping unobserved negative outcomes. Right-censor immature cohorts, and report migrated/preexisting members separately from newly acquired users.

### Small-group pilot

1. Add the minimum timestamp/exposure instrumentation and repair reliability/denominator issues. Observe the existing experience with clear coverage boundaries.
2. Test one behavioral change first: a chosen weekly target and personalized reminder timing. Record a proposed target effect and acceptable opt-out/message-volume change before the pilot.
3. With a small Discord group, use a staged rollout, report the individual counts, and ask members why they returned or stopped. A before/after change is descriptive and vulnerable to interview schedules, exams, seasonality, and novelty.
4. If there are enough independent members/groups, randomize an experiment at a level that handles social spillover. Keep reminder opt-outs and existing history invariant across groups. Predefine the sample size/window from the baseline rather than inventing a promised lift.
5. Continue only if sustained solve/practice consistency improves without excessive reminders or reduced learning quality. D28 conclusions require a matured follow-up window; a quick increase in bot opens is insufficient.

The first product bet is **a weekly commitment members can keep, paired with timely reminders and an encouraging return after a miss**. The existing grid and durable history make that progress credible.
