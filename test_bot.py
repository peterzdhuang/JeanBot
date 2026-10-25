"""Transport-level callback tests without Discord login or a PostgreSQL server."""
import ast
import asyncio
import copy
import logging
import types
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tracker import new_guild, new_member

EASY_QUEST = {
    "slug": "two-sum", "name": "Two Sum", "url": "https://leetcode.com/problems/two-sum/",
    "difficulty": "Easy", "practice_difficulty": "Medium",
    "constraint": "Use a single pass and O(n) time; explain the hash-map invariant.",
    "constraint_reason": "Requires an invariant and a strict time bound.",
    "constraint_source_url": "https://leetcode.com/problems/two-sum/",
}


class FakeHTTPException(Exception):
    pass


class FakeAllowedMentions:
    def __init__(self, **kwargs):
        self.values = kwargs

    @staticmethod
    def none():
        return "none"


class FakeEmbed:
    def __init__(self, **kwargs):
        self.values, self.fields, self.image = kwargs, [], None

    def set_footer(self, **kwargs):
        self.footer = kwargs

    def set_author(self, **kwargs):
        self.author = kwargs

    def set_image(self, **kwargs):
        self.image = kwargs

    def add_field(self, **kwargs):
        self.fields.append(kwargs)


class FakeResponse:
    def __init__(self):
        self.done, self.defers, self.messages = False, 0, []
        self.send_modal = AsyncMock()

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        self.done = True
        self.defers += 1

    async def send_message(self, text, **kwargs):
        self.done = True
        self.messages.append(text)


class MemoryStore:
    def __init__(self, guild=None):
        self.data = guild or new_guild()
        self.fail_write = False

    @asynccontextmanager
    async def edit(self, guild_id):
        working = copy.deepcopy(self.data)
        yield working
        if self.fail_write:
            raise RuntimeError("database unavailable")
        self.data = working

    async def guild(self, guild_id):
        return copy.deepcopy(self.data)


def command_namespace():
    """Execute the actual callback and scheduler bodies with only transport replaced."""
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text())
    blocked = {"discord", "dotenv"}
    body = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            if not any(alias.name.split(".")[0] in blocked for alias in node.names):
                body.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module.split(".")[0] not in blocked:
            body.append(node)
        elif isinstance(node, ast.Assign):
            names = {part.id for target in node.targets for part in ast.walk(target) if isinstance(part, ast.Name)}
            if names & {"log", "PURPLE", "GREEN", "GOLD", "MUTED", "Difficulty", "Period", "BuddyAction"}:
                body.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "TrackerBot":
            for method in node.body:
                if isinstance(method, ast.AsyncFunctionDef) and method.name in {"process_guild", "find_report_message"}:
                    method.decorator_list = []
                    body.append(method)
    namespace = {"discord": types.SimpleNamespace(
        Embed=FakeEmbed, HTTPException=FakeHTTPException,
        File=lambda fp, filename: (fp, filename), Object=lambda **kwargs: types.SimpleNamespace(**kwargs),
        AllowedMentions=FakeAllowedMentions),
        "DashboardView": lambda *args, **kwargs: object(),
        "ReminderSettingsView": lambda *args, **kwargs: object(),
        "SubmitModal": lambda: "submit-modal",
        "ReminderTimeModal": lambda: "reminder-time-modal"}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), "exec"), namespace)
    return namespace


def component_method(namespace, class_name, method_name):
    """Load a real Discord UI callback body without needing the Discord package."""
    path = Path(__file__).with_name("bot.py")
    tree = ast.parse(path.read_text())
    component = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in component.body if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name)
    method.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                              method], type_ignores=[])
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), scope)
    return scope[method_name]


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = command_namespace()
        self.store = MemoryStore()
        self.ns["bot"] = types.SimpleNamespace(store=self.store, submit_lock=asyncio.Lock())
        self.now = datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
        self.ns["utcnow"] = lambda: self.now
        self.interaction = types.SimpleNamespace(
            guild_id=7, user=types.SimpleNamespace(id=1, display_name="Friend",
                display_avatar=types.SimpleNamespace(url="https://example.com/avatar.png")),
            response=FakeResponse(), followup=types.SimpleNamespace(send=AsyncMock()))

    async def submit(self, url="https://leetcode.com/problems/two-sum/submissions/12345/", **kwargs):
        await self.ns["submit"](self.interaction, url, **kwargs)

    def text(self):
        embed = self.interaction.followup.send.call_args.kwargs["embed"]
        return str(embed.values) + str(embed.fields)

    async def test_submit_retains_attempt_and_shows_grid_streak_and_progress(self):
        await self.submit(difficulty="Hard")
        member = self.store.data["members"]["1"]
        self.assertEqual(member["counts"], [0, 0, 1])
        self.assertEqual(member["submissions"][0]["submission_id"], "12345")
        self.assertIn("/submissions/12345/", self.text())
        self.assertIn("self-reported", self.text())
        self.assertIn("Today / streak", self.text())
        self.assertIn("This week", self.text())
        self.assertIn("This month", self.text())
        kwargs = self.interaction.followup.send.call_args.kwargs
        self.assertEqual(kwargs["file"][1], "activity.png")
        self.assertTrue(kwargs["file"][0].getvalue().startswith(b"\x89PNG"))
        self.assertEqual(member["wallet"], 0)
        self.assertEqual(member["xp"], 0)

    async def test_dedup_survives_new_callback_namespace_and_another_attempt(self):
        await self.submit()
        # No process-local duplicate cache exists: database-loaded records do the work.
        self.ns = command_namespace()
        self.ns["utcnow"] = lambda: self.now
        self.ns["bot"] = types.SimpleNamespace(store=self.store, submit_lock=asyncio.Lock())
        self.interaction.response = FakeResponse()
        await self.submit()
        self.assertIn("Already counted", self.interaction.followup.send.call_args.args[0])
        self.interaction.response = FakeResponse()
        await self.submit("https://leetcode.com/problems/two-sum/submissions/54321/")
        self.assertIn("Already counted", self.interaction.followup.send.call_args.args[0])
        self.assertEqual(self.store.data["members"]["1"]["solved"], 1)

    async def test_problem_only_and_invalid_links_do_not_count(self):
        for url in ("https://leetcode.com/problems/two-sum/", "https://example.com/problems/two-sum/submissions/1/"):
            self.interaction.response = FakeResponse()
            await self.submit(url)
            self.assertTrue(self.interaction.response.messages)
            self.assertEqual(self.store.data["members"], {})

    async def test_unknown_difficulty_counts_in_today_and_progress(self):
        await self.submit("https://leetcode.com/problems/unknown-future-problem/submissions/12345/")
        member = self.store.data["members"]["1"]
        self.assertEqual(member["unknown_count"], 1)
        self.assertEqual(self.ns["count_day"](member, date(2026, 10, 10)), 1)
        self.assertIn("Unknown", self.text())
        self.assertIn("**1** solves today", self.text())

    async def test_quest_shows_easy_constraint_and_attestation(self):
        self.store.data["quests"]["2026-10-10"] = copy.deepcopy(EASY_QUEST)
        await self.ns["quest"](self.interaction)
        self.assertIn("Easy + Medium-level constraint", self.text())
        self.assertIn(EASY_QUEST["constraint"], self.text())
        self.assertIn("attests", self.text())

    async def test_matched_easy_quest_confirmation_shows_constraint_without_verifying_difficulty(self):
        self.store.data["quests"]["2026-10-10"] = copy.deepcopy(EASY_QUEST)
        await self.submit(difficulty="Hard")
        self.assertIn("Easy + Medium-level constraint", self.text())
        self.assertIn(EASY_QUEST["constraint"], self.text())
        self.assertIn("self-attested", self.text())
        self.assertEqual(self.store.data["members"]["1"]["submissions"][0]["difficulty"], "Hard")
        self.assertTrue(self.store.data["members"]["1"]["recent"]["2026-10-10"]["quest_complete"])

    async def test_persisted_medium_quest_without_new_metadata_still_renders(self):
        quest = {key: value for key, value in EASY_QUEST.items()
                 if key in {"slug", "name", "url"}}
        quest["difficulty"] = "Medium"
        self.store.data["quests"]["2026-10-10"] = quest
        await self.ns["quest"](self.interaction)
        self.assertIn("Medium", self.text())
        self.assertNotIn("Required constraint", self.text())

    async def test_failed_commit_does_not_claim_success(self):
        self.store.fail_write = True
        with self.assertLogs("arena", logging.ERROR):
            await self.submit()
        self.assertEqual(self.store.data["members"], {})
        self.assertIn("Couldn't load or save", self.interaction.followup.send.call_args.args[0])

    async def test_image_render_failure_keeps_committed_solve_and_text(self):
        self.ns["render_activity"] = lambda *args: (_ for _ in ()).throw(RuntimeError("render failed"))
        with self.assertLogs("arena", logging.ERROR):
            await self.submit()
        self.assertEqual(self.store.data["members"]["1"]["solved"], 1)
        self.assertNotIn("file", self.interaction.followup.send.call_args.kwargs)
        self.assertIn("This week", self.text())

    async def test_attachment_delivery_failure_retries_text_without_recounting(self):
        self.interaction.followup.send.side_effect = [FakeHTTPException("attachment failed"), None]
        await self.submit()
        self.assertEqual(self.store.data["members"]["1"]["solved"], 1)
        self.assertEqual(self.interaction.followup.send.await_count, 2)
        self.assertNotIn("file", self.interaction.followup.send.call_args.kwargs)
        self.assertIn("View submission", self.text())

    async def test_reminder_opt_out_keeps_all_history(self):
        await self.submit()
        before = copy.deepcopy(self.store.data["members"]["1"])
        self.interaction.response = FakeResponse()
        await self.ns["reminders"](self.interaction, False)
        member = self.store.data["members"]["1"]
        self.assertFalse(member["reminders"])
        self.assertEqual(member["submissions"], before["submissions"])
        self.assertEqual(member["solved"], before["solved"])

    async def test_join_includes_assigned_problem_and_attempt_copy_instructions(self):
        await self.ns["join"](self.interaction, "UTC")
        self.assertIn("https://leetcode.com/problems/", self.text())
        self.assertIn("Submissions", self.text())
        self.assertIn("Submit solve", self.text())
        self.assertIn("4 days/week", self.text())
        self.assertEqual(len(self.store.data["quests"]), 1)
        member = self.store.data["members"]["1"]
        self.assertEqual(member["reminder_time"], "20:00")
        self.assertFalse(member["late_reminder"])

    async def test_goal_changes_target_without_rewriting_history(self):
        await self.submit()
        before = copy.deepcopy(self.store.data["members"]["1"])
        self.interaction.response = FakeResponse()
        await self.ns["goal"](self.interaction, 3)
        member = self.store.data["members"]["1"]
        self.assertEqual(member["weekly_goal"], 3)
        self.assertEqual(member["submissions"], before["submissions"])
        self.assertIn("1/3 days/week", self.text())
        self.interaction.response = FakeResponse()
        await self.ns["goal"](self.interaction, 2)
        self.assertEqual(self.store.data["members"]["1"]["weekly_goal"], 3)
        self.assertIn("3", self.interaction.followup.send.call_args.args[0])

    async def test_personal_reminder_time_and_late_slot_are_configurable(self):
        await self.submit()
        self.interaction.response = FakeResponse()
        await self.ns["reminders"](self.interaction, reminder_time="19:30", late_reminder=True)
        member = self.store.data["members"]["1"]
        self.assertEqual(member["reminder_time"], "19:30")
        self.assertTrue(member["late_reminder"])
        self.assertIn("19:30", self.text())
        self.interaction.response = FakeResponse()
        await self.ns["reminders"](self.interaction, reminder_time="25:00")
        self.assertEqual(self.store.data["members"]["1"]["reminder_time"], "19:30")
        self.assertIn("HH:MM", self.interaction.followup.send.call_args.args[0])

    async def test_snooze_button_persists_pause_without_changing_deadline(self):
        await self.submit()
        original = copy.deepcopy(self.store.data["members"]["1"])
        self.interaction.response = FakeResponse()
        callback = component_method(self.ns, "DashboardView", "snooze")
        await callback(object(), self.interaction, None)
        member = self.store.data["members"]["1"]
        self.assertEqual(member["snoozed_until"], "2026-10-10T19:00:00+00:00")
        self.assertEqual(member["timezone"], original["timezone"])
        self.assertEqual(member["submissions"], original["submissions"])

    async def test_timezone_change_uses_next_deadline_without_resetting_streak(self):
        await self.submit()
        before = copy.deepcopy(self.store.data["members"]["1"])
        self.interaction.response = FakeResponse()
        await self.ns["timezone_command"](self.interaction, "UTC")
        member = self.store.data["members"]["1"]
        self.assertEqual(member["timezone"], before["timezone"])
        self.assertEqual(member["pending_timezone"]["timezone"], "UTC")
        self.assertEqual(member["streak"], before["streak"])
        self.assertEqual(member["submissions"], before["submissions"])
        self.assertIn("current day keeps its existing deadline", self.text())

    async def test_buddy_progress_requires_acceptance_and_can_be_removed(self):
        await self.submit()
        self.store.data["members"]["2"] = new_member(date(2026, 10, 10), "UTC")
        friend = types.SimpleNamespace(id=2, bot=False)
        self.interaction.response = FakeResponse()
        await self.ns["buddy"](self.interaction, "invite", friend)
        self.assertEqual(self.ns["buddy_progress"](self.store.data, 1, self.now), [])
        accepted = types.SimpleNamespace(
            guild_id=7, user=types.SimpleNamespace(id=2, display_name="Study friend",
                display_avatar=types.SimpleNamespace(url="https://example.com/avatar.png")),
            response=FakeResponse(), followup=types.SimpleNamespace(send=AsyncMock()))
        await self.ns["buddy"](accepted, "accept", types.SimpleNamespace(id=1, bot=False))
        self.assertEqual(self.ns["buddy_progress"](self.store.data, 1, self.now)[0]["user_id"], "2")
        self.interaction.response = FakeResponse()
        await self.ns["progress_command"](self.interaction)
        self.assertIn("study buddies", self.text())
        self.assertIn("<@2>", self.text())
        self.interaction.response = FakeResponse()
        await self.ns["buddy"](self.interaction, "remove", friend)
        self.assertEqual(self.ns["buddy_progress"](self.store.data, 1, self.now), [])
        self.assertEqual(self.store.data["members"]["1"]["solved"], 1)

    async def test_submit_modal_uses_same_persistence_and_duplicate_rules_as_slash(self):
        modal = types.SimpleNamespace(
            attempt_url=types.SimpleNamespace(value="https://leetcode.com/problems/two-sum/submissions/12345/"),
            problem_url=types.SimpleNamespace(value=""), difficulty=types.SimpleNamespace(value="hard"))
        callback = component_method(self.ns, "SubmitModal", "on_submit")
        await callback(modal, self.interaction)
        self.assertEqual(self.store.data["members"]["1"]["counts"], [0, 0, 1])
        self.assertIn("View submission", self.text())
        self.interaction.response = FakeResponse()
        await self.submit()
        self.assertEqual(self.store.data["members"]["1"]["solved"], 1)
        self.assertIn("Already counted", self.interaction.followup.send.call_args.args[0])

    async def test_persistent_submit_button_opens_modal(self):
        callback = component_method(self.ns, "DashboardView", "open_submit")
        await callback(object(), self.interaction, None)
        self.interaction.response.send_modal.assert_awaited_once_with("submit-modal")

    async def test_reminder_disable_button_is_private_and_preserves_schedule(self):
        await self.submit()
        self.interaction.response = FakeResponse()
        callback = component_method(self.ns, "ReminderSettingsView", "disable")
        await callback(object(), self.interaction, None)
        self.assertFalse(self.store.data["members"]["1"]["reminders"])
        self.assertEqual(self.store.data["members"]["1"]["reminder_time"], "20:00")
        self.assertTrue(self.interaction.followup.send.call_args.kwargs["ephemeral"])

    async def test_legacy_late_reminder_toggle_turns_inherited_slot_off(self):
        await self.submit()
        member = self.store.data["members"]["1"]
        member["reminder_time"] = None
        self.assertIn("02:15", self.ns["reminder_slots"](self.store.data, member))
        self.interaction.response = FakeResponse()
        callback = component_method(self.ns, "ReminderSettingsView", "toggle_late")
        await callback(object(), self.interaction, None)
        member = self.store.data["members"]["1"]
        self.assertNotIn("02:15", self.ns["reminder_slots"](self.store.data, member))
        self.assertEqual(member["solved"], 1)

    async def test_unjoined_profile_has_actionable_join_message(self):
        await self.ns["status"](self.interaction)
        self.assertIn("Join first", self.interaction.followup.send.call_args.args[0])

    def test_partial_progress_separates_unknown_days_from_goal_and_known_days(self):
        member = new_member(date(2026, 10, 1), "UTC")
        member["coverage_start"] = "2026-10-10"
        member["recent"]["2026-10-08"] = {"counts": [1, 0, 0], "quests": 0}
        text = self.ns["progress_text"](member, date(2026, 10, 10), "week", self.store.data)
        self.assertIn("1/4 days/week", text)
        self.assertIn("Known eligible days: 2", text)
        self.assertIn("Unknown: 4", text)
        self.assertIn("unknown days are excluded", text)

    def test_monthly_practice_day_survives_later_excusal(self):
        member = new_member(date(2026, 10, 10), "UTC")
        member["recent"]["2026-10-10"] = {"counts": [1, 0, 0], "quests": 0}
        member["excused_days"] = ["2026-10-10"]
        text = self.ns["progress_text"](member, date(2026, 10, 10), "month")
        self.assertIn("**1** days submitted", text)
        self.assertIn("**1** solves", text)
        self.assertIn("Excused: 1", text)

    async def test_all_daily_views_show_preserved_roster_deadline(self):
        self.store.data["members"]["1"] = new_member(date(2026, 10, 10), "UTC")
        stored_deadline = datetime(2026, 10, 10, 20, tzinfo=timezone.utc)
        self.store.data["roster"]["2026-10-10"] = {"1": {
            "timezone": "UTC", "deadline": stored_deadline.isoformat(), "excused": False,
            "closed": False, "count": 0, "quest": False, "links": []}}
        prefix = f"<t:{int(stored_deadline.timestamp())}:"
        for command, args in (("status", ()), ("quest", ()), ("join", ()), ("submit", (
                "https://leetcode.com/problems/two-sum/submissions/12345/",))):
            self.interaction.response = FakeResponse()
            await self.ns[command](self.interaction, *args)
            self.assertIn(prefix, self.text(), command)
        self.assertIn(prefix, "\n".join(self.ns["today_pages"](self.store.data, self.now)))

    async def test_comeback_confirmation_keeps_best_streak_and_total_visible(self):
        member = new_member(date(2026, 10, 1), "UTC")
        member.update(streak=7, longest=7, last_solved="2026-10-07", solved=7,
                      period_solved=7, completed_days=7)
        member["recent"] = {f"2026-10-{day:02}": {"counts": [1, 0, 0], "quests": 0}
                            for day in range(1, 8)}
        self.store.data["members"]["1"] = member
        await self.submit()
        saved = self.store.data["members"]["1"]
        self.assertEqual(saved["streak"], 1)
        self.assertEqual(saved["longest"], 7)
        self.assertEqual(saved["solved"], 8)
        self.assertIn("Welcome back", self.text())
        self.assertIn("previous solves and best streak still count", self.text())

    def test_dashboard_rows_and_modal_labels_fit_discord_limits(self):
        tree = ast.parse(Path(__file__).with_name("bot.py").read_text())
        dashboard = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DashboardView")
        rows = {}
        ids = []
        labels = []
        for method in dashboard.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            for decorator in method.decorator_list:
                if isinstance(decorator, ast.Call):
                    options = {keyword.arg: keyword.value.value for keyword in decorator.keywords
                               if isinstance(keyword.value, ast.Constant)}
                    row = options.get("row", 0)
                    rows[row] = rows.get(row, 0) + 1
                    ids.append(options["custom_id"])
                    labels.append(options["label"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(count <= 5 for count in rows.values()))
        self.assertTrue({"Submit solve", "Snooze 1h", "Reminders"} <= set(labels))
        modal = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SubmitModal")
        title = next(option.value.value for option in modal.keywords if option.arg == "title")
        self.assertLessEqual(len(title), 45)
        fields = [node for node in modal.body if isinstance(node, ast.Assign)]
        self.assertLessEqual(len(fields), 5)
        for field in fields:
            label = next(option.value.value for option in field.value.keywords if option.arg == "label")
            self.assertLessEqual(len(label), 45)

    def test_history_pagination_drops_no_records(self):
        member = new_member(date(2026, 1, 1), "UTC")
        member["submissions"] = [{"date": "2026-01-01", "slug": "two-sum", "difficulty": "Easy",
                                  "url": f"https://leetcode.com/submissions/detail/{index}/"} for index in range(150)]
        member["solved"] = 150
        pages = self.ns["history_pages"](member)
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page) <= 1800 for page in pages))
        combined = "\n".join(pages)
        for index in range(150):
            self.assertIn(f"/detail/{index}/", combined)

    def test_history_preserves_imported_name_without_a_slug(self):
        member = new_member(date(2026, 1, 1), "UTC")
        member["submissions"] = [{"date": "2026-01-01", "slug": None, "name": "Preserved legacy title"},
                                  {"date": "2026-01-02", "slug": None}]
        text = "\n".join(self.ns["history_pages"](member))
        self.assertIn("Preserved legacy title", text)
        self.assertIn("Unknown problem", text)

    def test_no_game_commands_remain_registered(self):
        tree = ast.parse(Path(__file__).with_name("bot.py").read_text())
        commands = {keyword.value.value for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                    for dec in node.decorator_list if isinstance(dec, ast.Call)
                    for keyword in dec.keywords if keyword.arg == "name" and isinstance(keyword.value, ast.Constant)}
        self.assertTrue({"submit", "quest", "progress", "history", "reminders"} <= commands)
        self.assertFalse({"shop", "buy", "pet", "feed", "equip", "vibecheck", "dungeon"} & commands)

    def test_log_redaction_and_original_cause(self):
        token, dsn = "FAKE_TOKEN_TEST", "postgresql://test:FAKE_PASSWORD_TEST@localhost/test"
        with patch.dict("os.environ", {"DISCORD_TOKEN": token, "DATABASE_URL": dsn}):
            original = NameError(f"missing_parser {token} {dsn} FAKE_PASSWORD_TEST")
            wrapped = RuntimeError("wrapper")
            wrapped.original = original
            with self.assertLogs("arena", logging.ERROR) as captured:
                result = self.ns["log_failure"]("Test command", wrapped)
        self.assertIs(result, original)
        output = "\n".join(captured.output)
        self.assertIn("missing_parser", output)
        self.assertNotIn(token, output)
        self.assertNotIn("FAKE_PASSWORD_TEST", output)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = command_namespace()
        self.now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        self.ns["utcnow"] = lambda: self.now
        self.guild = new_guild()
        self.guild["report_channel_id"] = 9
        self.store = MemoryStore(self.guild)
        self.channel = types.SimpleNamespace(send=AsyncMock(return_value=types.SimpleNamespace(id=123)))
        self.bot = types.SimpleNamespace(store=self.store, get_channel=lambda channel_id: self.channel,
                                        find_report_message=AsyncMock(return_value=None))

    def roster(self, amount=3):
        day = date(2026, 10, 10)
        self.store.data["roster"][day.isoformat()] = {
            str(index): {"timezone": "UTC", "deadline": "2026-10-11T03:15:00+00:00", "closed": True,
                         "excused": False, "count": index % 2, "quest": False,
                         "links": [f"https://leetcode.com/submissions/detail/{index}/"] if index % 2 else []}
            for index in range(1, amount + 1)}
        return day

    async def test_closed_report_lists_submitted_and_missed_without_pinging(self):
        day = self.roster()
        await self.ns["process_guild"](self.bot, 7)
        self.assertTrue(self.store.data["reports"][day.isoformat()]["complete"])
        call = self.channel.send.call_args
        self.assertIn("Submitted **2**", call.args[0])
        self.assertIn("Missed **1**", call.args[0])
        self.assertEqual(call.kwargs["allowed_mentions"], "none")
        await self.ns["process_guild"](self.bot, 7)
        self.assertEqual(self.channel.send.await_count, 1)

    async def test_report_waits_for_last_timezone_deadline(self):
        day = self.roster()
        self.store.data["roster"][day.isoformat()]["3"]["deadline"] = "2026-10-11T15:15:00+00:00"
        self.store.data["roster"][day.isoformat()]["3"]["closed"] = False
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_not_awaited()
        self.now = datetime(2026, 10, 11, 15, 16, tzinfo=timezone.utc)
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_awaited()

    async def test_new_join_after_report_scan_extends_deadline_before_preparation(self):
        day = self.roster()
        original_edit = self.store.edit
        edits = 0

        @asynccontextmanager
        async def racing_edit(guild_id):
            nonlocal edits
            edits += 1
            if edits == 2:
                self.store.data["roster"][day.isoformat()]["99"] = {
                    "timezone": "America/Los_Angeles", "deadline": "2026-10-11T15:15:00+00:00",
                    "closed": False, "excused": False, "count": 0, "quest": False, "links": []}
            async with original_edit(guild_id) as data:
                yield data

        self.store.edit = racing_edit
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_not_awaited()
        self.assertNotIn(day.isoformat(), self.store.data["reports"])

    async def test_new_join_after_report_preparation_defers_first_send(self):
        day = self.roster()
        original_edit = self.store.edit
        edits = 0

        @asynccontextmanager
        async def racing_edit(guild_id):
            nonlocal edits
            edits += 1
            if edits == 3:
                self.store.data["roster"][day.isoformat()]["99"] = {
                    "timezone": "America/Los_Angeles", "deadline": "2026-10-11T15:15:00+00:00",
                    "closed": False, "excused": False, "count": 0, "quest": False, "links": []}
            async with original_edit(guild_id) as data:
                yield data

        self.store.edit = racing_edit
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_not_awaited()
        self.assertFalse(self.store.data["reports"][day.isoformat()].get("complete", False))
        self.now = datetime(2026, 10, 11, 15, 16, tzinfo=timezone.utc)
        await self.ns["process_guild"](self.bot, 7)
        self.assertIn("<@99>", self.channel.send.call_args.args[0])

    async def test_paginated_report_resumes_after_failed_page(self):
        day = self.roster(100)
        self.channel.send.side_effect = [types.SimpleNamespace(id=1), FakeHTTPException("Discord down")]
        with self.assertRaises(FakeHTTPException):
            await self.ns["process_guild"](self.bot, 7)
        delivery = self.store.data["reports"][day.isoformat()]
        self.assertEqual(delivery["message_ids"], [1])
        first_page = delivery["pages"][0]
        self.channel.send.reset_mock(side_effect=True)
        self.channel.send.return_value = types.SimpleNamespace(id=2)
        await self.ns["process_guild"](self.bot, 7)
        delivery = self.store.data["reports"][day.isoformat()]
        self.assertTrue(delivery["complete"])
        self.assertEqual(len(delivery["message_ids"]), len(delivery["pages"]))
        self.assertTrue(all(call.args[0] != first_page for call in self.channel.send.call_args_list))
        combined = "\n".join(delivery["pages"])
        for index in range(1, 101):
            self.assertIn(f"<@{index}>", combined)

    async def test_uncertain_prior_send_is_reconciled_before_resending(self):
        day = self.roster()
        self.bot.find_report_message.return_value = 987
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.store.data["reports"][day.isoformat()]["message_ids"], [987])

    async def test_opted_out_members_never_receive_reminder(self):
        self.now = datetime(2026, 10, 11, 2, 30, tzinfo=timezone.utc)
        member = new_member(date(2026, 10, 10), "UTC")
        member["reminders"] = False
        self.store.data["members"]["1"] = member
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_not_awaited()

    async def test_pending_reminder_surfaces_mandatory_easy_constraint(self):
        self.now = datetime(2026, 10, 11, 2, 30, tzinfo=timezone.utc)
        self.store.data["members"]["1"] = new_member(date(2026, 10, 10), "UTC")
        self.store.data["members"]["1"]["late_reminder"] = True
        self.store.data["quests"]["2026-10-10"] = copy.deepcopy(EASY_QUEST)
        await self.ns["process_guild"](self.bot, 7)
        self.channel.send.assert_awaited_once()
        text = self.channel.send.call_args.args[0]
        self.assertIn("Easy + Medium-level constraint", text)
        self.assertIn(EASY_QUEST["constraint"], text)
        self.assertIn("attests", text)

    async def test_report_surfaces_mandatory_easy_constraint(self):
        self.roster()
        self.store.data["quests"]["2026-10-10"] = copy.deepcopy(EASY_QUEST)
        await self.ns["process_guild"](self.bot, 7)
        text = self.channel.send.call_args.args[0]
        self.assertIn("Easy + Medium-level constraint", text)
        self.assertIn(EASY_QUEST["constraint"], text)


if __name__ == "__main__":
    unittest.main()
