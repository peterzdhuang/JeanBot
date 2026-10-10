"""Small PostgreSQL snapshots: one row per guild and per player, no solve history."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from tracker import migrate_legacy, new_guild, new_member, utcnow

SCHEMA_VERSION = "2"
MAX_PLAYER_BYTES = 16_384
MAX_GUILD_BYTES = 4_096
NEW_PLAYER_LIMIT = 45 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS public.lc_arena_meta (
    key text PRIMARY KEY, value text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.lc_arena_guilds (
    guild_id bigint PRIMARY KEY,
    settings jsonb NOT NULL CHECK (octet_length(settings::text) <= 4096)
) WITH (fillfactor=80, autovacuum_vacuum_threshold=10, autovacuum_vacuum_scale_factor=0.02);
CREATE TABLE IF NOT EXISTS public.lc_arena_players (
    guild_id bigint NOT NULL REFERENCES public.lc_arena_guilds(guild_id),
    user_id bigint NOT NULL,
    state jsonb NOT NULL CHECK (
        octet_length(state::text) <= 16384 AND NOT state ? 'submissions'
    ),
    PRIMARY KEY (guild_id, user_id)
) WITH (fillfactor=80, autovacuum_vacuum_threshold=10, autovacuum_vacuum_scale_factor=0.02);
"""
GUILD_FIELDS = set(new_guild()) - {"members"}
PLAYER_FIELDS = set(new_member(utcnow().date(), "UTC"))


def encode_settings(guild: dict) -> str:
    if set(guild) != GUILD_FIELDS | {"members"}:
        raise ValueError("Unexpected guild storage fields.")
    value = json.dumps({key: guild[key] for key in sorted(GUILD_FIELDS)}, separators=(",", ":"), ensure_ascii=True)
    if len(value.encode()) > MAX_GUILD_BYTES:
        raise ValueError("Guild settings exceeded their storage budget.")
    return value


def encode_player(member: dict) -> str:
    """Enforce bounded fields at the persistence boundary."""
    from game import ITEMS, PERKS, PETS

    if set(member) != PLAYER_FIELDS:
        raise ValueError("Unexpected player fields; submission history is not supported.")
    if len(member["recent"]) > 30 or len(member["reminder_marks"]) > 2:
        raise ValueError("Player summaries exceeded their retention limit.")
    for state in member["recent"].values():
        if set(state) != {"counts", "quests"} or len(state["counts"]) != 3:
            raise ValueError("Only difficulty counts and quest bits may be retained.")
    if not set(member["inventory"]) <= set(ITEMS) or not set(member["equipped"]) <= {"title", "aura", "relic"}:
        raise ValueError("Unknown inventory fields.")
    if any(not isinstance(amount, int) or not 0 <= amount <= 99 for amount in member["inventory"].values()):
        raise ValueError("Invalid inventory quantity.")
    for kind, item_id in member["equipped"].items():
        if item_id not in ITEMS or ITEMS[item_id]["kind"] != kind or not member["inventory"].get(item_id):
            raise ValueError("Invalid equipped item.")
    pet = member["pet"]
    if pet is not None:
        if set(pet) != {"species", "name", "xp", "fullness", "fed_at"} or len(pet["name"]) > 32:
            raise ValueError("Invalid pet state.")
        if pet["species"] not in {species for species, _, _ in PETS}:
            raise ValueError("Unknown pet species.")
    run = member["run"]
    if run is not None:
        expected = {"day", "role", "floor", "hp", "max_hp", "loot", "status", "perks", "choices", "seed", "relic"}
        if set(run) != expected or len(run["perks"]) > 3 or len(run["choices"]) > 3:
            raise ValueError("Invalid dungeon state.")
        if not set(run["perks"] + run["choices"]) <= set(PERKS):
            raise ValueError("Unknown dungeon perks.")
    if member["wallet"] < 0:
        raise ValueError("Wallet cannot be negative.")
    value = json.dumps(member, separators=(",", ":"), ensure_ascii=True)
    if len(value.encode()) > MAX_PLAYER_BYTES:
        raise ValueError("Player state exceeded its 16 KB storage budget.")
    return value


class PostgresStore:
    def __init__(self, url: str):
        if not url:
            raise ValueError("Set DATABASE_URL in .env beside bot.py.")
        self._url = url
        self.pool = None

    async def open(self):
        import asyncpg

        # Port 6432 often means PgBouncer: avoid session-bound prepared statements.
        self.pool = await asyncpg.create_pool(
            dsn=self._url, min_size=1, max_size=2, statement_cache_size=0,
            timeout=15, command_timeout=20,
        )
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(781936284)")
                existing = await connection.fetchval("SELECT to_regclass('public.lc_arena_meta')")
                if existing:
                    version = await connection.fetchval(
                        "SELECT value FROM public.lc_arena_meta WHERE key='schema_version'"
                    )
                    if version != SCHEMA_VERSION:
                        raise ValueError("Existing lc_arena tables use a different schema; refusing to overwrite them.")
                else:
                    collisions = await connection.fetchval(
                        "SELECT to_regclass('public.lc_arena_guilds') IS NOT NULL "
                        "OR to_regclass('public.lc_arena_players') IS NOT NULL"
                    )
                    if collisions:
                        raise ValueError("Existing lc_arena tables have no schema marker; refusing to overwrite them.")
                await connection.execute(SCHEMA)
                await connection.execute(
                    "INSERT INTO public.lc_arena_meta(key,value) VALUES('schema_version',$1) "
                    "ON CONFLICT(key) DO NOTHING", SCHEMA_VERSION
                )

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _read(self, connection, guild_id: int, *, lock: bool = False) -> dict:
        suffix = " FOR UPDATE" if lock else ""
        raw = await connection.fetchval(
            "SELECT settings FROM public.lc_arena_guilds WHERE guild_id=$1" + suffix, guild_id
        )
        guild = new_guild() if raw is None else {**json.loads(raw), "members": {}}
        rows = await connection.fetch(
            "SELECT user_id,state FROM public.lc_arena_players WHERE guild_id=$1" + suffix, guild_id
        )
        guild["members"] = {str(row["user_id"]): json.loads(row["state"]) for row in rows}
        return guild

    async def guild(self, guild_id: int) -> dict:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await self._read(connection, guild_id)

    async def guild_ids(self) -> list[int]:
        async with self.pool.acquire() as connection:
            return [row["guild_id"] for row in await connection.fetch("SELECT guild_id FROM public.lc_arena_guilds")]

    @asynccontextmanager
    async def edit(self, guild_id: int):
        """Serialize all changes for one guild across processes; exceptions roll back."""
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", guild_id)
                guild = await self._read(connection, guild_id, lock=True)
                before_settings = encode_settings(guild)
                before = {key: encode_player(member) for key, member in guild["members"].items()}
                exists = await connection.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM public.lc_arena_guilds WHERE guild_id=$1)", guild_id
                )
                yield guild
                settings = encode_settings(guild)
                players = {key: encode_player(member) for key, member in guild["members"].items()}
                if not set(before) <= set(players):
                    raise ValueError("Removing player records is not supported by this operation.")
                if set(players) - set(before):
                    usage = await connection.fetchval("SELECT pg_database_size(current_database())")
                    if usage >= NEW_PLAYER_LIMIT:
                        raise ValueError("Database is near its 50 MB limit. Ask an admin to check /storage.")
                if not exists or settings != before_settings:
                    await connection.execute(
                        "INSERT INTO public.lc_arena_guilds(guild_id,settings) VALUES($1,$2::jsonb) "
                        "ON CONFLICT(guild_id) DO UPDATE SET settings=EXCLUDED.settings", guild_id, settings
                    )
                for key, value in players.items():
                    if before.get(key) != value:
                        await connection.execute(
                            "INSERT INTO public.lc_arena_players(guild_id,user_id,state) VALUES($1,$2,$3::jsonb) "
                            "ON CONFLICT(guild_id,user_id) DO UPDATE SET state=EXCLUDED.state",
                            guild_id, int(key), value,
                        )

    async def import_json(self, path: Path) -> tuple[int, int]:
        if not path.exists():
            return 0, 0
        # No modification of the legacy file, and no raw entries sent to PostgreSQL.
        source = json.loads(path.read_text(encoding="utf-8"))
        converted = migrate_legacy(source, utcnow())
        for guild in converted.values():
            encode_settings(guild)
            for member in guild["members"].values():
                encode_player(member)
        imported_guilds = imported_players = 0
        for key, legacy in converted.items():
            async with self.edit(int(key)) as current:
                if current["legacy_imported"]:
                    continue
                if current["members"]:
                    raise ValueError("Existing PostgreSQL players would overlap the JSON import; no records overwritten.")
                current.clear()
                current.update(legacy)
                imported_guilds += 1
                imported_players += len(legacy["members"])
        return imported_guilds, imported_players

    async def usage(self) -> dict:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT pg_database_size(current_database()) AS database_bytes, "
                "(SELECT coalesce(sum(pg_total_relation_size(oid)),0)::bigint FROM pg_class "
                "WHERE oid IN ('public.lc_arena_meta'::regclass,'public.lc_arena_guilds'::regclass,"
                "'public.lc_arena_players'::regclass)) AS bot_bytes, "
                "(SELECT count(*) FROM public.lc_arena_players) AS players"
            )
            return dict(row)


async def main():
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().with_name(".env"))
    parser = argparse.ArgumentParser(description="Initialize PostgreSQL and optionally import old JSON totals.")
    parser.add_argument("--import-json", type=Path)
    args = parser.parse_args()
    store = PostgresStore(os.getenv("DATABASE_URL", ""))
    try:
        await store.open()
        if args.import_json:
            guilds, players = await store.import_json(args.import_json)
            print(f"Imported {players} players across {guilds} guilds (aggregate counts only).")
        usage = await store.usage()
        print(f"Database: {usage['database_bytes'] / 1024**2:.2f} MiB; "
              f"bot tables: {usage['bot_bytes'] / 1024**2:.2f} MiB; players: {usage['players']}.")
    finally:
        await store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Connection exceptions may contain credentials. Don't print their raw text.
        print(f"Database setup failed ({type(exc).__name__}). Check DATABASE_URL, connectivity, and schema permissions.")
        raise SystemExit(1)
