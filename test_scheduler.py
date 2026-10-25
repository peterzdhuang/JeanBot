"""Delivery guarantees with durable in-memory state and simulated Discord history."""
import copy
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
import types
import unittest

from scheduler import process_guild
from tracker import new_guild, new_member, set_reminder_preferences

NOW = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
TODAY = NOW.date()
QUEST = {"name": "Add Two Numbers", "slug": "add-two-numbers", "difficulty": "Medium",
         "url": "https://leetcode.com/problems/add-two-numbers/"}


class MemoryStore:
    def __init__(self, data):
        self.data = data
        self.edits = 0
        self.fail_sent_commit = False
        self.before_edit = None

    @asynccontextmanager
    async def scheduler_edit(self, guild_id, now):
        self.edits += 1
        if self.before_edit:
            self.before_edit(self.data, self.edits)
        working = copy.deepcopy(self.data)
        yield working
        if self.fail_sent_commit and any(row.get("status") == "sent" for row in working.get("reminders", {}).values()):
            self.fail_sent_commit = False
            raise RuntimeError("commit failed after Discord accepted message")
        self.data = working


class Channel:
    def __init__(self):
        self.messages = []
        self.fail_after_accept = False
        self.history_forbidden = False
        self.history_arguments = []

    async def send(self, content, **kwargs):
        message = types.SimpleNamespace(id=len(self.messages) + 1, content=content,
                                        author=types.SimpleNamespace(id=77), kwargs=kwargs)
        self.messages.append(message)
        if self.fail_after_accept:
            self.fail_after_accept = False
            raise RuntimeError("connection lost after delivery")
        return message

    async def history(self, **kwargs):
        self.history_arguments.append(kwargs)
        if self.history_forbidden:
            raise PermissionError("Read Message History unavailable")
        for message in reversed(self.messages):
            yield message


class SchedulerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setup_fixture(self, amount=1):
        data = new_guild()
        data["report_channel_id"] = 9
        data["quests"][TODAY.isoformat()] = dict(QUEST)
        data["members"] = {str(i): new_member(TODAY, "UTC") for i in range(1, amount + 1)}
        self.now = NOW
        self.store = MemoryStore(data)
        self.channel = Channel()
        self.bot = types.SimpleNamespace(store=self.store, user=types.SimpleNamespace(id=77),
                                         get_channel=lambda channel_id: self.channel)

    async def tick(self, report_builder=None):
        await process_guild(self.bot, 7, clock=lambda: self.now,
                            mention_factory=lambda keys: list(keys) if keys else [], report_builder=report_builder)

    async def test_one_hundred_pending_members_are_two_batches_and_no_duplicate_tick(self):
        self.setup_fixture(100)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 2)
        for message in self.channel.messages:
            self.assertEqual(len(message.kwargs["allowed_mentions"]), 50)
            self.assertLessEqual(len(message.content), 2000)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 2)
        self.assertTrue(all(event["status"] == "sent" for event in self.store.data["reminders"].values()))

    async def test_new_members_do_not_receive_late_reminders_by_default(self):
        self.setup_fixture(100)
        await self.tick()
        self.now = NOW + timedelta(hours=6, minutes=15)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 2)

    async def test_explicit_late_slot_is_batched_and_delivered_once(self):
        self.setup_fixture(100)
        for member in self.store.data["members"].values():
            set_reminder_preferences(member, reminder_time="20:00", late_reminder=True)
        await self.tick()
        self.now = NOW + timedelta(hours=6, minutes=15)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 4)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 4)

    async def test_optout_and_completion_are_rechecked_after_intent_preparation(self):
        self.setup_fixture(3)
        def change_preferences(data, edit):
            if edit == 2:
                data["members"]["1"]["reminders"] = False
                data["members"]["2"]["recent"][TODAY.isoformat()] = {"counts": [0, 1, 0], "quests": 0}
        self.store.before_edit = change_preferences
        await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(self.channel.messages[0].kwargs["allowed_mentions"], ["3"])

    async def test_saved_send_is_recovered_after_failed_commit(self):
        self.setup_fixture()
        self.store.fail_sent_commit = True
        with self.assertRaises(RuntimeError):
            await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(next(iter(self.store.data["reminders"].values()))["status"], "attempting")
        await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        event = next(iter(self.store.data["reminders"].values()))
        self.assertEqual(event["status"], "sent")
        self.assertEqual(event["message_id"], 1)
        self.assertIsNone(self.channel.history_arguments[-1]["limit"])

    async def test_connection_loss_after_delivery_is_recovered(self):
        self.setup_fixture()
        self.channel.fail_after_accept = True
        with self.assertRaises(RuntimeError):
            await self.tick()
        await self.tick()
        self.assertEqual(len(self.channel.messages), 1)

    async def test_uncertain_send_is_deferred_when_history_is_unavailable(self):
        self.setup_fixture()
        self.store.fail_sent_commit = True
        with self.assertRaises(RuntimeError):
            await self.tick()
        self.channel.history_forbidden = True
        with self.assertLogs("arena", level="WARNING"):
            await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(next(iter(self.store.data["reminders"].values()))["status"], "uncertain")
        self.channel.history_forbidden = False
        await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(next(iter(self.store.data["reminders"].values()))["status"], "sent")

    async def test_no_due_members_needs_only_one_scoped_edit(self):
        self.setup_fixture(100)
        self.now = NOW - timedelta(hours=8)
        await self.tick()
        self.assertEqual(self.store.edits, 1)
        self.assertEqual(self.channel.messages, [])

    async def test_uncertain_old_slot_is_recovered_without_late_reping(self):
        self.setup_fixture()
        self.store.fail_sent_commit = True
        with self.assertRaises(RuntimeError):
            await self.tick()
        self.now += timedelta(hours=2)
        await self.tick()
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(next(iter(self.store.data["reminders"].values()))["status"], "sent")

    async def test_report_send_is_reconciled_and_final_result_survives(self):
        self.setup_fixture()
        self.now = NOW - timedelta(hours=8)
        label = (TODAY - timedelta(days=1)).isoformat()
        self.store.data["roster"][label] = {"1": {"timezone": "UTC", "deadline": f"{TODAY}T03:15:00+00:00",
                    "closed": True, "excused": False, "count": 1, "quest": False, "links": []}}
        builder = lambda guild_id, day, roster, quest: [f"Submitted 1\n`lc-report:{guild_id}:{day}:0`"]
        self.channel.fail_after_accept = True
        with self.assertRaises(RuntimeError):
            await self.tick(builder)
        await self.tick(builder)
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(self.store.data["reports"][label]["message_ids"], [1])
        self.assertTrue(self.store.data["reports"][label]["complete"])
        self.assertEqual(self.channel.messages[0].kwargs["allowed_mentions"], [])


if __name__ == "__main__":
    unittest.main()
