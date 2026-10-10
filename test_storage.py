"""Offline storage contract tests with a transactional fake, not a live PostgreSQL server."""
import asyncio
import copy
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from storage import NEW_PLAYER_LIMIT, PostgresStore, encode_player, encode_settings
from tracker import enroll, new_guild, new_member


class FakeConnection:
    def __init__(self):
        self.guilds, self.players = {}, {}
        self.queries = []
        self.database_bytes = 8 * 1024**2
        self.fail_player_write = False

    @asynccontextmanager
    async def transaction(self, **kwargs):
        guilds, players = copy.deepcopy(self.guilds), copy.deepcopy(self.players)
        try:
            yield self
        except BaseException:
            self.guilds, self.players = guilds, players
            raise

    async def execute(self, query, *args):
        self.queries.append((query, args))
        if query.startswith("INSERT INTO public.lc_arena_guilds"):
            self.guilds[args[0]] = args[1]
        if query.startswith("INSERT INTO public.lc_arena_players"):
            if self.fail_player_write:
                raise RuntimeError("injected write failure")
            self.players[(args[0], args[1])] = args[2]

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        if "SELECT settings FROM" in query:
            return self.guilds.get(args[0])
        if "SELECT EXISTS" in query:
            return args[0] in self.guilds
        if "pg_database_size" in query:
            return self.database_bytes
        raise AssertionError(f"Unexpected fake query: {query}")

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if "SELECT user_id,state" in query:
            return [{"user_id": user_id, "state": value} for (guild_id, user_id), value in self.players.items()
                    if guild_id == args[0]]
        if "SELECT guild_id" in query:
            return [{"guild_id": key} for key in self.guilds]
        raise AssertionError(f"Unexpected fake query: {query}")


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        async with self.lock:
            yield self.connection


class StorageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = PostgresStore("postgresql://test:not-real@localhost/test")
        self.store.pool = FakePool()
        self.connection = self.store.pool.connection
        self.day = date(2026, 10, 10)

    async def seed(self):
        async with self.store.edit(7) as data:
            enroll(data, 1, self.day, "PST")

    async def test_save_reload_compact_separate_rows(self):
        await self.seed()
        self.assertEqual(await self.store.guild_ids(), [7])
        saved = await self.store.guild(7)
        self.assertEqual(saved["members"]["1"]["timezone"], "America/Los_Angeles")
        self.assertNotIn("members", json.loads(self.connection.guilds[7]))
        self.assertEqual(set(self.connection.players), {(7, 1)})
        self.assertTrue(any("pg_advisory_xact_lock" in query for query, _ in self.connection.queries))

    async def test_unchanged_snapshot_does_not_write(self):
        await self.seed()
        self.connection.queries.clear()
        async with self.store.edit(7):
            pass
        self.assertFalse(any(query.startswith("INSERT") for query, _ in self.connection.queries))

    async def test_player_mutation_only_updates_its_row(self):
        await self.seed()
        self.connection.queries.clear()
        async with self.store.edit(7) as data:
            data["members"]["1"]["wallet"] += 5
        inserts = [query for query, _ in self.connection.queries if query.startswith("INSERT")]
        self.assertEqual(len(inserts), 1)
        self.assertIn("lc_arena_players", inserts[0])

    async def test_callback_exception_rolls_back(self):
        await self.seed()
        with self.assertRaises(ValueError):
            async with self.store.edit(7) as data:
                data["members"]["1"]["wallet"] = 500
                raise ValueError("stop")
        self.assertEqual((await self.store.guild(7))["members"]["1"]["wallet"], 0)

    async def test_db_write_failure_rolls_back_settings_and_players(self):
        await self.seed()
        self.connection.fail_player_write = True
        with self.assertRaises(RuntimeError):
            async with self.store.edit(7) as data:
                data["report_channel_id"] = 999
                data["members"]["1"]["wallet"] = 500
        saved = await self.store.guild(7)
        self.assertIsNone(saved["report_channel_id"])
        self.assertEqual(saved["members"]["1"]["wallet"], 0)

    async def test_new_joins_rejected_near_capacity(self):
        self.connection.database_bytes = NEW_PLAYER_LIMIT
        with self.assertRaises(ValueError):
            await self.seed()
        self.assertEqual(self.connection.guilds, {})
        self.assertEqual(self.connection.players, {})

    async def test_existing_player_update_allowed_near_capacity(self):
        await self.seed()
        self.connection.database_bytes = NEW_PLAYER_LIMIT
        async with self.store.edit(7) as data:
            data["members"]["1"]["wallet"] = 2
        self.assertEqual((await self.store.guild(7))["members"]["1"]["wallet"], 2)

    async def test_unknown_history_field_refused(self):
        await self.seed()
        with self.assertRaises(ValueError):
            async with self.store.edit(7) as data:
                data["members"]["1"]["submissions"] = ["two-sum"]
        self.assertNotIn("submissions", (await self.store.guild(7))["members"]["1"])

    async def test_player_removal_not_silently_persisted(self):
        await self.seed()
        with self.assertRaises(ValueError):
            async with self.store.edit(7) as data:
                del data["members"]["1"]
        self.assertIn("1", (await self.store.guild(7))["members"])

    async def test_import_idempotent_and_leaves_old_file_untouched(self):
        legacy = {"version": 1, "guilds": {"7": {
            "timezone": "America/Vancouver",
            "members": {"1": {"joined": "2026-01-01", "active": True}},
            "submissions": [{"user_id": "1", "date": "2026-01-01", "slug": "two-sum",
                             "url": "https://leetcode.com/problems/two-sum/"}]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            # Test fixtures use write_text; application migrations never write legacy files.
            path.write_text(json.dumps(legacy), encoding="utf-8")
            original = path.read_bytes()
            self.assertEqual(await self.store.import_json(path), (1, 1))
            async with self.store.edit(7) as data:
                data["members"]["1"]["wallet"] = 17
            self.assertEqual(await self.store.import_json(path), (0, 0))
            self.assertEqual(path.read_bytes(), original)
        saved = await self.store.guild(7)
        self.assertEqual(saved["members"]["1"]["solved"], 1)
        self.assertEqual(saved["members"]["1"]["wallet"], 17)
        self.assertNotIn("two-sum", self.connection.players[(7, 1)])

    async def test_import_refuses_overlapping_players(self):
        await self.seed()
        legacy = {"version": 1, "guilds": {"7": {"timezone": "UTC",
                  "members": {"1": {"joined": "2026-01-01", "active": True}}, "submissions": []}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with self.assertRaises(ValueError):
                await self.store.import_json(path)
        self.assertFalse((await self.store.guild(7))["legacy_imported"])

    async def test_serialized_transactions_preserve_wallet_updates(self):
        await self.seed()
        async def increment():
            async with self.store.edit(7) as data:
                previous = data["members"]["1"]["wallet"]
                await asyncio.sleep(0)
                data["members"]["1"]["wallet"] = previous + 1
        await asyncio.gather(*(increment() for _ in range(20)))
        self.assertEqual((await self.store.guild(7))["members"]["1"]["wallet"], 20)

    def test_nested_history_and_unbounded_state_refused(self):
        member = new_member(self.day, "PST")
        member["recent"][self.day.isoformat()] = {"counts": [1, 0, 0], "quests": 0, "slug": "two-sum"}
        with self.assertRaises(ValueError):
            encode_player(member)
        member["recent"] = {str(index): {"counts": [0, 0, 0], "quests": 0} for index in range(31)}
        with self.assertRaises(ValueError):
            encode_player(member)

    def test_unknown_guild_fields_refused(self):
        guild = new_guild()
        guild["submissions"] = []
        with self.assertRaises(ValueError):
            encode_settings(guild)

    def test_missing_url_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
            PostgresStore("")


if __name__ == "__main__":
    unittest.main()
