import json
import unittest
from datetime import date, datetime, timedelta, timezone as UTC

from catalog import DIFFICULTY, POOLS, resolve_difficulty
from storage import encode_player, encode_settings
from tracker import (
    REWARD_LIMIT, challenge_day, daily_quest, daily_report, deadline, enroll,
    migrate_legacy, new_guild, rankings, record, reminder_due, stats, timezone,
)

NOW = datetime(2026, 10, 10, 18, tzinfo=UTC.utc)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.guild = new_guild()
        self.day = date(2026, 10, 10)
        enroll(self.guild, 1, self.day, "PST")

    def solve(self, day=None, slug="two-sum", **kwargs):
        return record(self.guild, 1, day or self.day, slug, guild_id=7, now=NOW, **kwargs)

    def test_problem_and_submission_links(self):
        from tracker import problem_from_url
        expected = ("two-sum", "https://leetcode.com/problems/two-sum/")
        for suffix in ("", "description/", "submissions/", "submissions/12345/"):
            self.assertEqual(problem_from_url(expected[1] + suffix), expected)
        for invalid in (
            "https://evil.com/problems/two-sum/", "http://leetcode.com/problems/two-sum/",
            "https://leetcode.com/submissions/detail/12345/", expected[1] + "?x=1",
            "https://leetcode.com:abc/problems/two-sum/", expected[1] + "#answer",
            "https://user:password@leetcode.com/problems/two-sum/",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                problem_from_url(invalid)

    def test_aliases_and_invalid_zone(self):
        for alias, region in (("pst", "America/Los_Angeles"), ("EST", "America/New_York"),
                              ("MST", "America/Denver"), ("CST", "America/Chicago"),
                              ("PDT", "America/Los_Angeles"), ("UTC", "UTC")):
            self.assertEqual(timezone(alias).key, region)
        self.assertEqual(timezone("America/Phoenix").key, "America/Phoenix")
        with self.assertRaises(ValueError):
            timezone("Not/A/Timezone")

    def test_cutoff_before_at_and_after(self):
        for minute, expected in ((14, date(2026, 10, 9)), (15, self.day), (16, self.day)):
            local = datetime(2026, 10, 10, 3, minute, tzinfo=timezone("PST"))
            self.assertEqual(challenge_day(local, "PST"), expected)
        self.assertEqual(challenge_day(datetime(2026, 10, 10, 0, 1, tzinfo=timezone("EST")), "EST"),
                         date(2026, 10, 9))
        with self.assertRaises(ValueError):
            challenge_day(datetime(2026, 10, 10), "PST")

    def test_spring_dst_cutoff(self):
        day = date(2026, 3, 7)
        end = deadline(day, "PST")
        self.assertEqual(end, datetime(2026, 3, 8, 10, 15, tzinfo=UTC.utc))
        self.assertEqual(challenge_day(end - timedelta(seconds=1), "PST"), day)
        self.assertEqual(challenge_day(end, "PST"), day + timedelta(days=1))
        self.assertEqual(deadline(day, "PST") - deadline(day - timedelta(days=1), "PST"), timedelta(hours=23))

    def test_fall_dst_cutoff_and_repeated_hour(self):
        day = date(2026, 10, 31)
        end = deadline(day, "PST")
        self.assertEqual(end, datetime(2026, 11, 1, 11, 15, tzinfo=UTC.utc))
        self.assertEqual(deadline(day, "PST") - deadline(day - timedelta(days=1), "PST"), timedelta(hours=25))
        for fold in (0, 1):
            repeated = datetime(2026, 11, 1, 1, 30, tzinfo=timezone("PST"), fold=fold)
            self.assertEqual(challenge_day(repeated, "PST"), day)

    def test_counts_only_no_problem_identifiers(self):
        self.solve()
        snapshot = encode_player(self.guild["members"]["1"])
        for forbidden in ("two-sum", "leetcode", "slug", "url", "submissions"):
            self.assertNotIn(forbidden, snapshot)
        self.assertEqual(json.loads(snapshot)["counts"], [1, 0, 0])

    def test_difficulty_rewards_daily_bonus_once(self):
        first = self.solve()
        medium = self.solve(slug="add-two-numbers")
        hard = self.solve(slug="trapping-rain-water")
        self.assertEqual((first["coins"], first["xp"]), (30, 75))
        self.assertEqual((medium["coins"], medium["xp"]), (50, 100))
        self.assertEqual((hard["coins"], hard["xp"]), (100, 180))
        result = stats(self.guild, 1, self.day)
        self.assertEqual((result["solved"], result["counts"], result["completed_days"]), (3, [1, 1, 1], 1))

    def test_difficulty_known_and_unknown(self):
        self.assertEqual(resolve_difficulty("add-two-numbers"), "Medium")
        self.assertEqual(resolve_difficulty("a-new-problem", "Hard"), "Hard")
        self.assertEqual(resolve_difficulty("a-new-problem"), "Easy")
        with self.assertRaises(ValueError):
            resolve_difficulty("two-sum", "Hard")
        with self.assertRaises(ValueError):
            resolve_difficulty("a-new-problem", "Impossible")

    def test_catalog_exact_counts_and_rotation(self):
        self.assertEqual({name: len(pool) for name, pool in POOLS.items()}, {"Easy": 25, "Medium": 75, "Hard": 25})
        self.assertEqual(len(DIFFICULTY), 125)
        for difficulty, pool in POOLS.items():
            quests = [daily_quest(7, self.day + timedelta(days=offset), difficulty) for offset in range(len(pool))]
            self.assertEqual({quest["slug"] for quest in quests}, set(pool))
            self.assertTrue(all(quest["difficulty"] == difficulty for quest in quests))

    def test_quest_claims_survive_restart_once_each_difficulty(self):
        for difficulty in POOLS:
            slug = daily_quest(7, self.day, difficulty)["slug"]
            self.assertTrue(self.solve(slug=slug)["quest"])
        saved = json.loads(encode_player(self.guild["members"]["1"]))
        self.guild["members"]["1"] = saved
        for difficulty in POOLS:
            slug = daily_quest(7, self.day, difficulty)["slug"]
            self.assertFalse(self.solve(slug=slug)["quest"])
        self.assertEqual(saved["quests"], 3)
        self.assertEqual(saved["recent"][self.day.isoformat()]["quests"], 7)
        self.assertTrue(self.solve(day=self.day + timedelta(days=1),
                                   slug=daily_quest(7, self.day + timedelta(days=1))["slug"])["quest"])

    def test_daily_reward_limit_counts_still_increment(self):
        for index in range(REWARD_LIMIT):
            self.solve(slug=f"new-problem-{index}")
        before = stats(self.guild, 1, self.day)
        extra = self.solve(slug="new-problem-over-limit")
        after = stats(self.guild, 1, self.day)
        self.assertFalse(extra["rewarded"])
        self.assertEqual((extra["coins"], extra["xp"]), (0, 0))
        self.assertEqual(after["solved"], before["solved"] + 1)
        quest = self.solve(slug=daily_quest(7, self.day, "Hard")["slug"])
        self.assertEqual((quest["coins"], quest["xp"]), (50, 200))

    def test_streak_misses_and_gaps(self):
        self.solve()
        self.solve(day=self.day + timedelta(days=1))
        result = stats(self.guild, 1, self.day + timedelta(days=2))
        self.assertEqual((result["streak"], result["missed"], result["longest"]), (2, 0, 2))
        result = stats(self.guild, 1, self.day + timedelta(days=3))
        self.assertEqual((result["streak"], result["missed"]), (0, 1))
        self.solve(day=self.day + timedelta(days=3))
        result = stats(self.guild, 1, self.day + timedelta(days=3))
        self.assertEqual((result["streak"], result["missed"], result["longest"]), (1, 1, 2))
        with self.assertRaises(ValueError):
            self.solve(day=self.day)

    def test_long_lifetime_retains_only_thirty_summaries(self):
        for offset in range(200):
            self.solve(day=self.day + timedelta(days=offset), slug=f"problem-{offset}")
        member = self.guild["members"]["1"]
        result = stats(self.guild, 1, self.day + timedelta(days=200))
        self.assertEqual((result["streak"], result["longest"], result["solved"], result["missed"]), (200, 200, 200, 0))
        self.assertEqual(len(member["recent"]), 30)
        self.assertLess(len(encode_player(member)), 5000)
        self.assertIn("👑 Unstoppable", result["badges"])

    def test_rejoin_keeps_wallet_pet_and_lifetime_counts(self):
        self.solve()
        member = self.guild["members"]["1"]
        member["reminders"] = False
        member["active"] = False
        member["left_day"] = self.day.isoformat()
        tomorrow = self.day + timedelta(days=3)
        self.assertTrue(enroll(self.guild, 1, tomorrow, "EST"))
        result = stats(self.guild, 1, tomorrow)
        self.assertEqual((result["solved"], result["streak"], result["missed"], result["wallet"]), (1, 0, 0, 30))
        self.assertFalse(self.guild["members"]["1"]["reminders"])
        self.assertEqual(self.guild["members"]["1"]["timezone"], "America/New_York")

    def test_active_timezone_changes_require_rejoin(self):
        self.assertFalse(enroll(self.guild, 1, self.day, "PST"))
        with self.assertRaises(ValueError):
            enroll(self.guild, 1, self.day, "EST")

    def test_equivalent_timezone_region_does_not_reset_streak(self):
        self.solve()
        self.assertFalse(enroll(self.guild, 1, self.day, "America/Vancouver"))
        self.assertEqual(stats(self.guild, 1, self.day)["streak"], 1)

    def test_rejoin_cannot_repeat_daily_quest_or_reset_reward_cap(self):
        quest_slug = daily_quest(7, self.day)["slug"]
        self.solve(slug=quest_slug)
        for index in range(REWARD_LIMIT - 1):
            self.solve(slug=f"problem-{index}")
        self.guild["members"]["1"].update(active=False, left_day=self.day.isoformat())
        enroll(self.guild, 1, self.day, "PST")
        extra = self.solve(slug=quest_slug)
        self.assertEqual((extra["coins"], extra["xp"], extra["quest"]), (0, 0, False))
        result = stats(self.guild, 1, self.day)
        self.assertEqual((result["streak"], result["missed"], result["completed_days"]), (1, 0, 1))

    def test_leave_freezes_missed_days(self):
        self.solve()
        member = self.guild["members"]["1"]
        member.update(active=False, left_day=self.day.isoformat())
        self.assertEqual(stats(self.guild, 1, self.day + timedelta(days=100))["missed"], 0)

    def test_rankings_use_each_persons_day(self):
        now = datetime(2026, 10, 11, 8, tzinfo=UTC.utc)  # 1 AM PT, 4 AM ET.
        enroll(self.guild, 2, self.day, "EST")
        self.solve()
        record(self.guild, 2, self.day, "two-sum", guild_id=7, now=NOW)
        record(self.guild, 2, self.day + timedelta(days=1), "two-sum", guild_id=7, now=now)
        rows = rankings(self.guild, now)
        self.assertEqual([key for key, _ in rows], ["2", "1"])
        self.assertEqual([result["streak"] for _, result in rows], [2, 1])
        self.guild["members"]["2"]["active"] = False
        self.assertEqual(len(rankings(self.guild, now)), 1)

    def test_daily_report_with_zone_filter(self):
        enroll(self.guild, 2, self.day, "EST")
        self.solve()
        self.assertEqual(daily_report(self.guild, self.day), (["1"], ["2"]))
        self.assertEqual(daily_report(self.guild, self.day, "America/New_York"), ([], ["2"]))

    def test_personal_evening_reminders_and_opt_out(self):
        member = self.guild["members"]["1"]
        now = datetime(2026, 10, 10, 20, 15, tzinfo=timezone("PST"))
        self.assertEqual(reminder_due(self.guild, member, now), "20:00")
        member["reminder_marks"]["20:00"] = self.day.isoformat()
        self.assertIsNone(reminder_due(self.guild, member, now))
        member["reminder_marks"] = {}
        member["reminders"] = False
        self.assertIsNone(reminder_due(self.guild, member, now))
        member["reminders"] = True
        self.solve()
        self.assertIsNone(reminder_due(self.guild, member, now))

    def test_after_midnight_reminder_uses_previous_challenge_day(self):
        member = self.guild["members"]["1"]
        now = datetime(2026, 10, 11, 2, 20, tzinfo=timezone("PST"))
        self.assertEqual(challenge_day(now, "PST"), self.day)
        self.assertEqual(reminder_due(self.guild, member, now), "02:15")
        member["reminder_marks"]["02:15"] = self.day.isoformat()
        self.assertIsNone(reminder_due(self.guild, member, now))
        self.assertIsNone(reminder_due(self.guild, member, now.replace(hour=10)))

    def test_badges_and_snapshot_restore(self):
        for offset in range(7):
            self.solve(day=self.day + timedelta(days=offset))
        self.guild["members"]["1"] = json.loads(encode_player(self.guild["members"]["1"]))
        result = stats(self.guild, 1, self.day + timedelta(days=8))
        self.assertEqual((result["longest"], result["streak"]), (7, 0))
        self.assertIn("⚡ Week Warrior", result["badges"])

    def test_legacy_migration_preserves_totals_without_history(self):
        source = {"version": 1, "guilds": {"7": {
            "timezone": "America/Vancouver", "report_channel_id": 99,
            "members": {"1": {"joined": "2026-10-01", "active": True, "reminders": False}},
            "submissions": [
                {"user_id": "1", "date": "2026-10-08", "slug": "two-sum", "url": "https://secret-url/"},
                {"user_id": "1", "date": "2026-10-09", "slug": "two-sum", "url": "https://secret-url/", "quest_xp": 50},
                {"user_id": "1", "date": "2026-10-09", "slug": "add-two-numbers", "url": "https://secret-url/"},
            ]}}}
        data = migrate_legacy(source, NOW)["7"]
        result = stats(data, 1, self.day)
        self.assertEqual((result["solved"], result["streak"], result["missed"], result["xp"]), (3, 2, 7, 200))
        self.assertEqual(result["counts"], [2, 1, 0])
        self.assertFalse(data["members"]["1"]["reminders"])
        encoded = encode_settings(data) + encode_player(data["members"]["1"])
        for forbidden in ("two-sum", "add-two-numbers", "https://", "submissions"):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(data["legacy_imported"])

    def test_legacy_invalid_version_rejected(self):
        with self.assertRaises(ValueError):
            migrate_legacy({"version": 99, "guilds": {}}, NOW)

    def test_migration_preserves_all_lifetime_counts_after_old_rejoin(self):
        source = {"version": 1, "guilds": {"7": {"timezone": "UTC",
                  "members": {"1": {"joined": "2026-10-10", "active": True}},
                  "submissions": [
                      {"user_id": "1", "date": "2026-10-01", "slug": "two-sum"},
                      {"user_id": "1", "date": "2026-10-10", "slug": "add-two-numbers"},
                  ]}}}
        data = migrate_legacy(source, NOW)["7"]
        result = stats(data, 1, self.day)
        self.assertEqual((result["solved"], result["period_solved"], result["counts"]), (2, 1, [1, 1, 0]))

    def test_migration_before_cutoff_does_not_discard_old_calendar_day_solves(self):
        source = {"version": 1, "guilds": {"7": {"timezone": "UTC",
                  "members": {"1": {"joined": "2026-10-10", "active": True}},
                  "submissions": [{"user_id": "1", "date": "2026-10-10", "slug": "two-sum"}]}}}
        now = NOW.replace(hour=2)
        data = migrate_legacy(source, now)["7"]
        result = stats(data, 1, date(2026, 10, 9))
        self.assertEqual((result["solved"], result["today_count"], result["missed"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
