"""Tracking contracts: history preservation, personal clocks, and honest progress."""
import copy
import json
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone as UTC
from unittest.mock import patch

from tracker import (
    activity_days, challenge_day, daily_quest, deadline, due_reports, enroll,
    leave_member, migrate_legacy, new_guild, progress, record, reminder_due,
    stats, submission_from_url, sync_roster, timezone, upgrade_member, rankings,
    set_weekly_goal, set_reminder_preferences, snooze_member, reminder_slots,
    change_timezone, apply_pending_timezone, member_day, invite_buddy, accept_buddy, remove_buddy, buddy_progress,
)

NOW = datetime(2026, 10, 10, 18, tzinfo=UTC.utc)
DAY = date(2026, 10, 10)
QUEST = {"slug": "add-two-numbers", "name": "Add Two Numbers", "difficulty": "Medium",
         "url": "https://leetcode.com/problems/add-two-numbers/", "catalog_version": "test"}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.guild = new_guild()
        enroll(self.guild, 1, DAY, "PST")
        self.catalog = patch.dict(sys.modules, {"quest_catalog": types.SimpleNamespace(
            select_quest=lambda guild_id, day: dict(QUEST))})
        self.catalog.start()
        self.addCleanup(self.catalog.stop)
        self.sequence = 0

    def solve(self, day=DAY, slug="two-sum", **kwargs):
        self.sequence += 1
        submission_id = str(self.sequence)
        return record(self.guild, 1, day, slug, guild_id=7, now=NOW,
                      submission_id=submission_id,
                      submission_url=f"https://leetcode.com/problems/{slug}/submissions/{submission_id}/",
                      **kwargs)

    def test_rejoin_today_materializes_roster_after_inactive_cursor_advanced(self):
        leave_member(self.guild, 1, NOW)
        later = NOW + timedelta(days=4)
        current_day = DAY + timedelta(days=4)
        sync_roster(self.guild, later)
        member = self.guild["members"]["1"]
        self.assertEqual(member["roster_cursor"], current_day.isoformat())
        self.assertNotIn("1", self.guild["roster"].get(current_day.isoformat(), {}))
        enroll(self.guild, 1, current_day, "PST")
        sync_roster(self.guild, later)
        self.assertIn("1", self.guild["roster"][current_day.isoformat()])
        snapshot = copy.deepcopy(self.guild)
        sync_roster(self.guild, later)
        self.assertEqual(self.guild, snapshot)

    def test_rejoin_current_roster_does_not_rewrite_existing_closed_snapshot(self):
        sync_roster(self.guild, NOW)
        row = self.guild["roster"][DAY.isoformat()]["1"]
        row.update(closed=True, count=99, quest=True, links=["https://preserved/"])
        snapshot = copy.deepcopy(row)
        self.guild["members"]["1"]["roster_cursor"] = DAY.isoformat()
        sync_roster(self.guild, NOW)
        self.assertEqual(row, snapshot)

    def test_eastward_dateline_jump_rejected_without_inventing_misses(self):
        self.guild = new_guild()
        enroll(self.guild, 1, DAY, "Etc/GMT+12")
        now = datetime(2026, 10, 11, 0, 0, tzinfo=UTC.utc)
        record(self.guild, 1, DAY, "two-sum", guild_id=7, now=now, submission_id="1")
        member = self.guild["members"]["1"]
        history = copy.deepcopy(member["submissions"])
        with self.assertRaisesRegex(ValueError, "intermediate timezone"):
            change_timezone(self.guild, 1, "Pacific/Kiritimati", now)
        self.assertEqual(member["timezone"], "Etc/GMT+12")
        self.assertIsNone(member["pending_timezone"])
        self.assertEqual(member["streak"], 1)
        self.assertEqual(member["submissions"], history)
        self.assertEqual(set(self.guild["roster"]), {DAY.isoformat()})

    def test_westward_dateline_transition_keeps_labels_and_ranking_continuous(self):
        self.guild = new_guild()
        enroll(self.guild, 1, DAY, "Pacific/Kiritimati")
        now = datetime(2026, 10, 10, 0, 0, tzinfo=UTC.utc)
        record(self.guild, 1, DAY, "two-sum", guild_id=7, now=now, submission_id="1")
        pending = change_timezone(self.guild, 1, "Etc/GMT+12", now)
        cutoff = datetime.fromisoformat(pending["effective_at"])
        sync_roster(self.guild, cutoff)
        member = self.guild["members"]["1"]
        next_day = DAY + timedelta(days=1)
        self.assertEqual(member_day(member, cutoff), next_day)
        self.assertEqual(set(self.guild["roster"]), {DAY.isoformat(), next_day.isoformat()})
        record(self.guild, 1, next_day, "two-sum", guild_id=7, now=cutoff, submission_id="2")
        self.assertEqual(rankings(self.guild, cutoff)[0][1]["streak"], 2)
        self.assertEqual(stats(self.guild, 1, next_day)["missed"], 0)

    def test_excused_active_member_has_no_reminder(self):
        member = self.guild["members"]["1"]
        member["excused_days"] = [DAY.isoformat()]
        now = datetime(2026, 10, 10, 20, 15, tzinfo=timezone("PST"))
        self.assertIsNone(reminder_due(self.guild, member, now))

    def test_reminder_reads_effective_pending_zone_before_scheduler_applies_it(self):
        member = self.guild["members"]["1"]
        member["timezone"] = "UTC"
        member["periods"][0]["timezone"] = "UTC"
        pending = change_timezone(self.guild, 1, "PST", NOW)
        next_day = DAY + timedelta(days=1)
        now = datetime(2026, 10, 11, 20, 15, tzinfo=timezone("PST"))
        self.assertGreater(now, datetime.fromisoformat(pending["effective_at"]))
        self.assertEqual(member_day(member, now), next_day)
        self.assertEqual(reminder_due(self.guild, member, now), "20:00")

    def test_weekly_goal_meets_four_practice_days_without_daily_pressure(self):
        member = self.guild["members"]["1"]
        self.assertEqual(member["weekly_goal"], 4)
        for offset in range(4):
            self.solve(DAY + timedelta(days=offset))
        result = progress(member, DAY + timedelta(days=3), guild=self.guild)
        # The week boundary is Monday; Saturday/Sunday were the prior week.
        self.assertEqual(result["goal_days"], 2)
        set_weekly_goal(member, 3)
        self.solve(DAY + timedelta(days=4))
        self.assertTrue(progress(member, DAY + timedelta(days=4), guild=self.guild)["goal_met"])
        for invalid in (2, 8, True, 3.5):
            with self.assertRaises(ValueError):
                set_weekly_goal(member, invalid)

    def test_upgrade_adds_goal_without_altering_historical_values_or_inherited_reminders(self):
        member = self.guild["members"]["1"]
        member.pop("weekly_goal")
        member.pop("reminder_time")
        member["reminders"] = False
        before = copy.deepcopy(member)
        upgrade_member(member, DAY)
        self.assertEqual(member["weekly_goal"], 4)
        self.assertEqual(reminder_slots(self.guild, member), ["20:00", "02:15"])
        for key in before:
            self.assertEqual(member[key], before[key])

    def test_progress_omits_unknown_and_excused_days_without_losing_coverage(self):
        member = self.guild["members"]["1"]
        member["periods"][0]["start"] = "2026-10-05"
        member["excused_days"] = ["2026-10-09"]
        self.solve()
        result = progress(member, DAY, guild=self.guild)
        self.assertEqual((result["days"], result["eligible"], result["total_eligible"]), (1, 1, 6))
        self.assertEqual((result["unknown_days"], result["excused_days"]), (4, 1))
        self.assertTrue(result["partial"])

    def test_excused_solve_day_still_counts_toward_practice_goal(self):
        self.solve()
        leave_member(self.guild, 1, NOW)
        result = progress(self.guild["members"]["1"], DAY, guild=self.guild)
        self.assertEqual((result["solves"], result["days"], result["eligible"], result["goal_days"]), (1, 0, 0, 1))

    def test_new_members_have_only_evening_reminder_until_late_opt_in(self):
        member = self.guild["members"]["1"]
        self.assertEqual(reminder_slots(self.guild, member), ["20:00"])
        late = datetime(2026, 10, 11, 2, 20, tzinfo=timezone("PST"))
        self.assertIsNone(reminder_due(self.guild, member, late))
        set_reminder_preferences(member, reminder_time="19:30", late_reminder=True)
        self.assertEqual(reminder_slots(self.guild, member), ["19:30", "02:15"])
        self.assertEqual(reminder_due(self.guild, member, late), "02:15")
        before = copy.deepcopy(member)
        for value in ("25:00", "1:30", "03:00", "20:99"):
            with self.assertRaises(ValueError):
                set_reminder_preferences(member, reminder_time=value)
            self.assertEqual(member, before)

    def test_snooze_delays_pending_nudge_once_and_respects_optout(self):
        member = self.guild["members"]["1"]
        now = datetime(2026, 10, 10, 20, 0, tzinfo=timezone("PST"))
        until = snooze_member(member, now)
        self.assertIsNone(reminder_due(self.guild, member, now + timedelta(minutes=59)))
        slot = reminder_due(self.guild, member, now + timedelta(minutes=60))
        self.assertEqual(slot, f"snooze@{until}")
        member["reminder_marks"][f"{DAY}:{slot}"] = DAY.isoformat()
        self.assertIsNone(reminder_due(self.guild, member, now + timedelta(minutes=61)))
        member["reminder_marks"].clear()
        set_reminder_preferences(member, enabled=False)
        self.assertIsNone(reminder_due(self.guild, member, now + timedelta(minutes=61)))

    def test_snooze_does_not_ping_after_personal_deadline_or_after_submission(self):
        member = self.guild["members"]["1"]
        final = datetime(2026, 10, 11, 2, 45, tzinfo=timezone("PST"))
        snooze_member(member, final, minutes=60)
        self.assertIsNone(reminder_due(self.guild, member, final + timedelta(minutes=60)))
        now = datetime(2026, 10, 10, 20, 0, tzinfo=timezone("PST"))
        snooze_member(member, now, minutes=60)
        self.solve()
        self.assertIsNone(reminder_due(self.guild, member, now + timedelta(minutes=60)))

    def test_future_timezone_change_preserves_open_deadline_streak_and_all_records(self):
        self.solve()
        member = self.guild["members"]["1"]
        history = copy.deepcopy(member["submissions"])
        pending = change_timezone(self.guild, 1, "EST", NOW)
        original_cutoff = deadline(DAY, "PST")
        self.assertEqual(datetime.fromisoformat(pending["effective_at"]), original_cutoff)
        self.assertEqual(member["timezone"], "America/Los_Angeles")
        self.assertFalse(apply_pending_timezone(self.guild, 1, original_cutoff - timedelta(seconds=1)))
        sync_roster(self.guild, original_cutoff)
        self.assertEqual(member["timezone"], "America/New_York")
        self.assertEqual(member_day(member, original_cutoff), DAY + timedelta(days=1))
        self.assertEqual(member["streak"], 1)
        self.assertEqual(member["submissions"], history)
        self.assertEqual(self.guild["roster"][DAY.isoformat()]["1"]["deadline"], original_cutoff.isoformat())
        self.assertFalse(apply_pending_timezone(self.guild, 1, original_cutoff))

    def test_leaving_before_pending_timezone_takes_effect_creates_no_new_obligation(self):
        member = self.guild["members"]["1"]
        change_timezone(self.guild, 1, "EST", NOW)
        leave_member(self.guild, 1, NOW)
        periods = copy.deepcopy(member["periods"])
        cutoff = deadline(DAY, "PST")
        sync_roster(self.guild, cutoff + timedelta(days=2))
        self.assertFalse(member["active"])
        self.assertEqual(member["periods"], periods)
        self.assertEqual(member["timezone"], "America/New_York")
        for later in (DAY + timedelta(days=1), DAY + timedelta(days=2)):
            self.assertNotIn("1", self.guild["roster"].get(later.isoformat(), {}))
        rejoin_day = DAY + timedelta(days=3)
        enroll(self.guild, 1, rejoin_day, "CST")
        self.assertEqual([p for p in member["periods"] if p["end"] is None],
                         [{"start": rejoin_day.isoformat(), "end": None, "timezone": "America/Chicago"}])

    def test_rejoin_discards_stale_pending_timezone(self):
        member = self.guild["members"]["1"]
        change_timezone(self.guild, 1, "EST", NOW)
        leave_member(self.guild, 1, NOW)
        enroll(self.guild, 1, DAY, "PST")
        self.assertIsNone(member["pending_timezone"])
        sync_roster(self.guild, deadline(DAY, "PST"))
        self.assertEqual(member["timezone"], "America/Los_Angeles")
        self.assertEqual(sum(p["end"] is None for p in member["periods"]), 1)

    def test_westward_timezone_transition_never_reverses_date_or_duplicates_day(self):
        member = self.guild["members"]["1"]
        member["timezone"] = "UTC"
        member["periods"][0]["timezone"] = "UTC"
        self.solve()
        pending = change_timezone(self.guild, 1, "PST", NOW)
        cutoff = datetime.fromisoformat(pending["effective_at"])
        self.assertEqual(challenge_day(cutoff, "PST"), DAY)  # New zone has not rolled over.
        self.assertEqual(member_day(member, cutoff), DAY + timedelta(days=1))
        sync_roster(self.guild, cutoff)
        next_day = DAY + timedelta(days=1)
        record(self.guild, 1, next_day, "two-sum", guild_id=7, now=cutoff, submission_id="123")
        self.assertEqual(member["streak"], 2)
        self.assertEqual(member["solved"], 2)
        self.assertEqual(set(member["recent"]), {DAY.isoformat(), next_day.isoformat()})
        with self.assertRaises(ValueError):
            record(self.guild, 1, DAY, "another-problem", guild_id=7, now=cutoff, submission_id="124")

    def test_roster_cursor_quiet_tick_is_noop_and_catches_up_once(self):
        sync_roster(self.guild, NOW)
        snapshot = copy.deepcopy(self.guild)
        sync_roster(self.guild, NOW + timedelta(seconds=10))
        self.assertEqual(self.guild, snapshot)
        self.assertEqual(self.guild["members"]["1"]["roster_cursor"], DAY.isoformat())
        sync_roster(self.guild, NOW + timedelta(days=3))
        self.assertEqual(self.guild["members"]["1"]["roster_cursor"], (DAY + timedelta(days=3)).isoformat())
        self.assertEqual(len(self.guild["roster"]), 4)

    def test_buddy_progress_requires_explicit_consent_from_invited_member(self):
        enroll(self.guild, 2, DAY, "UTC")
        invite_buddy(self.guild, 1, 2)
        self.assertEqual(buddy_progress(self.guild, 1, NOW), [])
        with self.assertRaises(ValueError):
            accept_buddy(self.guild, 1, 2)  # Inviter cannot accept their own invitation.
        accept_buddy(self.guild, 2, 1)
        self.assertEqual(buddy_progress(self.guild, 1, NOW)[0]["user_id"], "2")
        self.assertEqual(buddy_progress(self.guild, 2, NOW)[0]["user_id"], "1")
        remove_buddy(self.guild, 2, 1)
        self.assertEqual(buddy_progress(self.guild, 1, NOW), [])
        self.assertEqual(buddy_progress(self.guild, 2, NOW), [])
        with self.assertRaises(ValueError):
            invite_buddy(self.guild, 1, 1)

    def test_comeback_acknowledges_return_without_manipulating_streak_or_totals(self):
        self.solve()
        result = self.solve(DAY + timedelta(days=3))
        self.assertTrue(result["comeback"])
        self.assertEqual(self.guild["members"]["1"]["streak"], 1)
        self.assertEqual(self.guild["members"]["1"]["solved"], 2)
        self.assertFalse(self.solve(DAY + timedelta(days=4))["comeback"])

    def test_aliases_and_invalid_zone(self):
        self.assertEqual(timezone("PST").key, "America/Los_Angeles")
        self.assertEqual(timezone("EST").key, "America/New_York")
        with self.assertRaises(ValueError):
            timezone("Not/A/Timezone")

    def test_cutoff_before_at_after(self):
        for minute, expected in ((14, DAY - timedelta(days=1)), (15, DAY), (16, DAY)):
            self.assertEqual(challenge_day(datetime(2026, 10, 10, 3, minute, tzinfo=timezone("PST")), "PST"), expected)
        with self.assertRaises(ValueError):
            challenge_day(datetime(2026, 10, 10), "UTC")

    def test_dst_has_wall_clock_cutoff(self):
        for day, hours in ((date(2026, 3, 7), 23), (date(2026, 10, 31), 25)):
            end = deadline(day, "PST")
            self.assertEqual(end - deadline(day - timedelta(days=1), "PST"), timedelta(hours=hours))
            self.assertEqual(challenge_day(end - timedelta(seconds=1), "PST"), day)
            self.assertEqual(challenge_day(end, "PST"), day + timedelta(days=1))

    def test_attempt_link_preserved_and_parameters_removed(self):
        result = submission_from_url("https://www.leetcode.com/problems/two-sum/submissions/123/?utm_source=x")
        self.assertEqual(result["url"], "https://leetcode.com/problems/two-sum/submissions/123/")
        self.assertEqual(result["submission_id"], "123")
        self.assertEqual(result["slug"], "two-sum")

    def test_general_attempt_requires_problem(self):
        url = "https://leetcode.com/submissions/detail/123/"
        with self.assertRaisesRegex(ValueError, "problem_url"):
            submission_from_url(url)
        self.assertEqual(submission_from_url(url, "https://leetcode.com/problems/two-sum/")["slug"], "two-sum")

    def test_problem_only_and_lookalike_links_rejected(self):
        for url in ("https://leetcode.com/problems/two-sum/", "https://leetcode.com.evil.com/problems/two-sum/submissions/1/",
                    "http://leetcode.com/problems/two-sum/submissions/1/", "https://u:p@leetcode.com/problems/two-sum/submissions/1/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                submission_from_url(url)

    def test_difficulty_is_trusted_and_unknown_is_not_easy(self):
        self.solve(difficulty="Hard")
        result = self.solve(slug="a-new-question")
        member = self.guild["members"]["1"]
        self.assertEqual(member["counts"], [0, 0, 1])
        self.assertEqual(member["unknown_count"], 1)
        self.assertEqual(result["difficulty"], "Unknown")
        self.assertEqual(stats(self.guild, 1, DAY)["today_count"], 2)

    def test_no_game_fields_are_changed(self):
        member = self.guild["members"]["1"]
        member.update(wallet=99, xp=123, pet={"legacy": True}, run={"legacy": True})
        before = copy.deepcopy(member)
        self.solve()
        for key in ("wallet", "xp", "pet", "run", "inventory", "equipped"):
            self.assertEqual(member[key], before[key])

    def test_duplicate_attempt_and_problem_survive_serialization(self):
        first = self.solve()
        self.guild = json.loads(json.dumps(self.guild))
        with self.assertRaisesRegex(ValueError, "Already counted this submission"):
            record(self.guild, 1, DAY, "another-problem", guild_id=7, submission_id=first["submission"]["submission_id"])
        with self.assertRaisesRegex(ValueError, "Already counted that problem"):
            self.solve()
        self.assertEqual(self.guild["members"]["1"]["solved"], 1)

    def test_daily_quest_persists_and_ignores_declared_difficulty(self):
        assigned = daily_quest(7, DAY, self.guild)
        with patch.dict(sys.modules, {"quest_catalog": types.SimpleNamespace(select_quest=lambda *_: None)}):
            self.assertEqual(daily_quest(7, DAY, self.guild), assigned)
        result = self.solve(slug=QUEST["slug"], difficulty="Easy")
        self.assertTrue(result["quest"])
        self.assertTrue(self.guild["members"]["1"]["recent"][DAY.isoformat()]["quest_complete"])

    def test_catalog_failure_does_not_block_ordinary_tracking(self):
        with patch.dict(sys.modules, {"quest_catalog": types.SimpleNamespace(select_quest=unconfigured)}):
            self.solve()
        self.assertEqual(self.guild["members"]["1"]["solved"], 1)

    def test_all_history_survives_long_period(self):
        for offset in range(100):
            self.solve(DAY + timedelta(days=offset))
        member = self.guild["members"]["1"]
        self.assertEqual(len(member["recent"]), 100)
        self.assertEqual(len(member["submissions"]), 100)
        self.assertEqual(stats(self.guild, 1, DAY + timedelta(days=99))["streak"], 100)

    def test_streak_stays_until_open_day_closes(self):
        self.solve()
        self.solve(DAY + timedelta(days=1))
        self.assertEqual(stats(self.guild, 1, DAY + timedelta(days=2))["streak"], 2)
        self.assertEqual(stats(self.guild, 1, DAY + timedelta(days=3))["streak"], 0)

    def test_rejoin_keeps_history_opt_out_and_unknown_fields(self):
        self.solve()
        member = self.guild["members"]["1"]
        member.update(reminders=False, custom_legacy={"keep": 1})
        leave_member(self.guild, 1, NOW)
        enroll(self.guild, 1, DAY + timedelta(days=3), "EST")
        self.assertEqual(len(member["submissions"]), 1)
        self.assertEqual(member["custom_legacy"], {"keep": 1})
        self.assertFalse(member["reminders"])
        self.assertEqual(member["solved"], 1)
        with self.assertRaises(ValueError):
            enroll(self.guild, 1, DAY + timedelta(days=3), "PST")

    def test_upgrade_retains_entire_legacy_snapshot(self):
        member = self.guild["members"]["1"]
        for key in ("submissions", "coverage_start", "unknown_count", "periods", "quest_start"):
            del member[key]
        member["ancient_field"] = {"old": True}
        before = copy.deepcopy(member)
        upgrade_member(member, DAY)
        for key in before:
            self.assertEqual(member[key], before[key])
        self.assertEqual(member["coverage_start"], DAY.isoformat())

    def test_progress_counts_days_separately_and_unknown_history(self):
        member = self.guild["members"]["1"]
        member["periods"][0]["start"] = "2026-10-01"
        self.solve()
        self.solve(slug="add-two-numbers")
        result = progress(member, DAY)
        self.assertEqual((result["solves"], result["days"], result["eligible"]), (2, 1, 1))
        self.assertTrue(result["partial"])
        self.assertEqual((result["total_eligible"], result["unknown_days"]), (6, 5))
        self.assertEqual((result["quests"], result["quest_eligible"]), (1, 1))

    def test_quest_denominator_counts_only_assigned_days(self):
        member = self.guild["members"]["1"]
        self.assertEqual(progress(member, DAY, guild=self.guild)["quest_eligible"], 0)
        daily_quest(7, DAY, self.guild)
        self.assertEqual(progress(member, DAY + timedelta(days=1), guild=self.guild)["quest_eligible"], 1)
        with patch.dict(sys.modules, {"quest_catalog": types.SimpleNamespace(select_quest=unconfigured)}):
            self.solve()
        self.assertEqual(progress(member, DAY)["quest_eligible"], 1)  # Existing assignment still applies.

    def test_scheduler_assigns_current_quest_without_inventing_past_assignments(self):
        sync_roster(self.guild, NOW, guild_id=7)
        self.assertEqual(set(self.guild["quests"]), {DAY.isoformat()})

    def test_progress_excludes_inactive_interval(self):
        member = self.guild["members"]["1"]
        member["coverage_start"] = "2026-10-01"
        member["periods"] = [{"start": "2026-10-05", "end": "2026-10-06"}, {"start": "2026-10-09", "end": None}]
        self.assertEqual(progress(member, DAY)["eligible"], 4)
        self.assertEqual(progress(member, date(2026, 11, 1), "month")["eligible"], 1)

    def test_activity_grid_monday_aligned_with_distinct_unknown_days(self):
        self.solve()
        member = self.guild["members"]["1"]
        member["periods"][0]["start"] = "2026-08-01"
        cells = activity_days(member, DAY)
        self.assertEqual(len(cells), 84)
        self.assertEqual(date.fromisoformat(cells[0]["date"]).weekday(), 0)
        self.assertEqual(cells[-2]["count"], 1)  # Saturday
        self.assertEqual(cells[-1]["status"], "future")
        self.assertIn("unknown", {cell["status"] for cell in cells})
        self.assertIn("inactive", {cell["status"] for cell in cells})

    def test_reminder_optout_and_durable_marker(self):
        member = self.guild["members"]["1"]
        now = datetime(2026, 10, 10, 20, 15, tzinfo=timezone("PST"))
        self.assertEqual(reminder_due(self.guild, member, now), "20:00")
        member["reminder_marks"]["2026-10-10:20:00"] = DAY.isoformat()
        self.assertIsNone(reminder_due(self.guild, member, now))
        member["reminder_marks"].clear()
        member["reminders"] = False
        self.assertIsNone(reminder_due(self.guild, member, now))

    def test_dst_nonexistent_reminder_slot_is_skipped(self):
        self.guild["reminder_times"] = ["02:15"]
        member = self.guild["members"]["1"]
        now = datetime(2026, 3, 8, 3, 0, tzinfo=timezone("PST"))
        self.assertIsNone(reminder_due(self.guild, member, now))

    def test_report_waits_for_last_timezone_and_freezes_previous_day(self):
        enroll(self.guild, 2, DAY, "EST")
        sync_roster(self.guild, NOW)
        self.solve()
        first_cutoff = deadline(DAY, "EST")
        self.assertEqual(due_reports(self.guild, first_cutoff), [])
        self.assertTrue(self.guild["roster"][DAY.isoformat()]["2"]["closed"])
        record(self.guild, 2, DAY + timedelta(days=1), "two-sum", guild_id=7, now=first_cutoff,
               submission_id="9", submission_url="https://leetcode.com/problems/two-sum/submissions/9/")
        reports = due_reports(self.guild, deadline(DAY, "PST"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0][1]["2"]["count"], 0)
        self.assertEqual(reports[0][1]["1"]["count"], 1)

    def test_partial_report_retries_but_completed_report_does_not(self):
        sync_roster(self.guild, NOW)
        self.guild["reports"][DAY.isoformat()] = {"pages": [], "complete": False}
        self.assertEqual(len(due_reports(self.guild, deadline(DAY, "PST"))), 1)
        self.guild["reports"][DAY.isoformat()]["complete"] = True
        self.assertEqual(due_reports(self.guild, deadline(DAY, "PST")), [])

    def test_leave_excuses_open_day_keeps_report_deadline(self):
        sync_roster(self.guild, NOW)
        leave_member(self.guild, 1, NOW)
        row = self.guild["roster"][DAY.isoformat()]["1"]
        self.assertTrue(row["excused"])
        self.assertEqual(row["deadline"], deadline(DAY, "PST").isoformat())

    def test_published_report_roster_cannot_expand_between_pages(self):
        sync_roster(self.guild, NOW)
        self.guild["reports"][DAY.isoformat()] = {"complete": False, "message_ids": [123]}
        enroll(self.guild, 2, DAY, "America/Adak")
        sync_roster(self.guild, NOW)
        self.assertNotIn("2", self.guild["roster"][DAY.isoformat()])
        with self.assertRaisesRegex(ValueError, "reported and closed"):
            record(self.guild, 2, DAY, "two-sum", guild_id=7, now=NOW)

    def test_roster_boundary_survives_delayed_first_scheduler_tick(self):
        member = self.guild["members"]["1"]
        self.assertEqual(member["roster_start"], DAY.isoformat())
        sync_roster(self.guild, NOW + timedelta(days=3))
        self.assertIn(DAY.isoformat(), self.guild["roster"])

    def test_same_day_rejoin_cannot_submit_into_closed_timezone_result(self):
        enroll(self.guild, 2, DAY, "EST")
        sync_roster(self.guild, NOW)
        closed = deadline(DAY, "EST")
        leave_member(self.guild, 2, closed)
        enroll(self.guild, 2, DAY, "PST")  # Still the previous challenge day in Pacific time.
        with self.assertRaisesRegex(ValueError, "already closed"):
            record(self.guild, 2, DAY, "two-sum", guild_id=7, now=closed,
                   submission_id="99", submission_url="https://leetcode.com/problems/two-sum/submissions/99/")
        self.assertEqual(self.guild["members"]["2"]["solved"], 0)
        self.assertTrue(self.guild["roster"][DAY.isoformat()]["2"]["closed"])
        self.assertEqual(self.guild["roster"][DAY.isoformat()]["2"]["count"], 0)

    def test_rejoin_archives_previous_streak_summary(self):
        self.solve()
        self.solve(DAY + timedelta(days=1))
        now = deadline(DAY, "PST") + timedelta(hours=1)
        leave_member(self.guild, 1, now)
        enroll(self.guild, 1, DAY + timedelta(days=1), "PST")
        member = self.guild["members"]["1"]
        self.assertEqual(member["streak"], 1)  # Fresh period, same completed day.
        self.assertTrue(any(row["streak"] == 2 for row in member["tracking_period_history"]))
        self.assertEqual(len(member["submissions"]), 2)
        self.assertEqual(member["longest"], 2)

    def test_future_legacy_labels_are_preserved_without_blocking_new_solves(self):
        source = {"version": 1, "guilds": {"7": {"timezone": "UTC", "members": {
            "1": {"joined": "2020-01-01"}}, "submissions": [
            {"user_id": "1", "date": "2099-01-01", "slug": "two-sum", "url": "https://old/attempt"}]}}}
        self.guild = migrate_legacy(source, NOW)["7"]
        member = self.guild["members"]["1"]
        self.assertEqual(member["submissions"][0]["date"], "2099-01-01")
        self.assertIn("2099-01-01", member["recent"])
        self.assertIsNone(member["last_solved"])
        self.solve(slug="add-two-numbers")
        self.assertEqual(member["last_solved"], DAY.isoformat())
        self.assertEqual(member["solved"], 2)

    def test_future_aggregate_summary_does_not_poison_live_streak(self):
        member = self.guild["members"]["1"]
        member.update(last_solved="2099-01-01", completed_days=1, streak=1, longest=1)
        member["recent"]["2099-01-01"] = {"counts": [1, 0, 0], "quests": 0}
        self.solve()
        self.assertEqual(member["future_legacy_tracking"]["last_solved"], "2099-01-01")
        self.assertEqual(member["last_solved"], DAY.isoformat())
        self.assertEqual(member["streak"], 1)
        self.assertIn("2099-01-01", member["recent"])

    def test_catchup_roster_uses_original_enrollment_timezone(self):
        sync_roster(self.guild, NOW)
        tomorrow = DAY + timedelta(days=1)
        # Simulate an old period whose missing days need catch-up after a timezone change.
        member = self.guild["members"]["1"]
        member["periods"][0]["end"] = tomorrow.isoformat()
        member["periods"].append({"start": (tomorrow + timedelta(days=1)).isoformat(),
                                  "end": None, "timezone": "America/New_York"})
        member["timezone"] = "America/New_York"
        sync_roster(self.guild, deadline(tomorrow, "PST"))
        row = self.guild["roster"][tomorrow.isoformat()]["1"]
        self.assertEqual(row["timezone"], "America/Los_Angeles")
        self.assertEqual(row["deadline"], deadline(tomorrow, "PST").isoformat())

    def test_legacy_preserves_old_labels_raw_details_and_all_days(self):
        entries = [ {"user_id": "1", "date": "2020-01-01", "slug": "two-sum", "url": "https://old/attempt", "custom": 3},
                    {"user_id": "1", "date": "2026-10-10", "slug": "add-two-numbers", "quest_xp": 50} ]
        source = {"version": 1, "guilds": {"7": {"timezone": "UTC", "members": {
            "1": {"joined": "2020-01-01", "reminders": False}}, "submissions": entries}}}
        before = copy.deepcopy(source)
        member = migrate_legacy(source, NOW.replace(hour=2))["7"]["members"]["1"]
        self.assertEqual(source, before)
        self.assertEqual(member["solved"], 2)
        self.assertEqual(set(member["recent"]), {"2020-01-01", "2026-10-10"})
        self.assertEqual(member["submissions"][0]["raw"], entries[0])
        self.assertEqual(member["submissions"][0]["url"], "https://old/attempt")
        self.assertFalse(member["reminders"])
        self.assertNotIn("quest_complete", member["recent"]["2026-10-10"])


def unconfigured(*_):
    raise ValueError("Configure Top 300 catalog")


if __name__ == "__main__":
    unittest.main()
