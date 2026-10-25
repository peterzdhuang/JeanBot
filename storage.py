"""PostgreSQL snapshots with additive, durable LeetCode history and v2 backups."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, timedelta

from tracker import migrate_legacy, new_guild, new_member, upgrade_member, utcnow, challenge_day

SCHEMA_VERSION = "3"
MAX_PLAYER_BYTES = 16_384
MAX_GUILD_BYTES = 4_096
MEMBER_HISTORY = ("recent", "submissions")
GUILD_HISTORY = ("quests", "reports", "roster", "reminders", "buddies")
SCHEMA = """
CREATE TABLE IF NOT EXISTS public.lc_arena_meta (key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE IF NOT EXISTS public.lc_arena_guilds (
    guild_id bigint PRIMARY KEY, settings jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.lc_arena_players (
    guild_id bigint NOT NULL REFERENCES public.lc_arena_guilds(guild_id),
    user_id bigint NOT NULL, state jsonb NOT NULL,
    PRIMARY KEY (guild_id,user_id)
);
CREATE TABLE IF NOT EXISTS public.lc_tracker_history (
    guild_id bigint NOT NULL, user_id bigint NOT NULL, kind text NOT NULL,
    key text NOT NULL, payload jsonb NOT NULL,
    PRIMARY KEY (guild_id,user_id,kind,key)
);
CREATE TABLE IF NOT EXISTS public.lc_tracker_events (
    guild_id bigint NOT NULL, kind text NOT NULL, key text NOT NULL,
    payload jsonb NOT NULL, PRIMARY KEY (guild_id,kind,key)
);
CREATE INDEX IF NOT EXISTS lc_tracker_history_day_idx
    ON public.lc_tracker_history(guild_id,kind,key) WHERE kind='recent';
CREATE INDEX IF NOT EXISTS lc_tracker_submission_day_idx
    ON public.lc_tracker_history(guild_id,(coalesce(payload->>'date',payload->>'day')))
    WHERE kind='submissions';
CREATE TABLE IF NOT EXISTS public.lc_tracker_backups (
    kind text NOT NULL, key text NOT NULL, payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (kind,key)
);
"""


def dumps(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True, sort_keys=True)


def decode(value):
    return json.loads(value) if isinstance(value, str) else copy.deepcopy(value)


def encode_settings(guild: dict) -> str:
    """Keep all old/unknown settings, separating growing tracking collections."""
    return dumps({key: value for key, value in guild.items()
                  if key not in ("members", *GUILD_HISTORY)})


def encode_player(member: dict) -> str:
    """Never discard archived fields or put unbounded history into old capped rows."""
    return dumps({key: value for key, value in member.items() if key not in MEMBER_HISTORY})


def history_rows(guild):
    rows = {}
    for user_id, member in guild["members"].items():
        for key, value in member.get("recent", {}).items():
            rows[(int(user_id), "recent", key)] = dumps(value)
        legacy_occurrences = {}
        for item in member.get("submissions", []):
            if not isinstance(item, dict):
                raise ValueError("Submission history must contain records.")
            # Full-content keys preserve even legacy entries without submission IDs.
            if item.get("submission_id"):
                key = str(item["submission_id"])
            else:
                digest = hashlib.sha256(dumps(item).encode()).hexdigest()
                occurrence = legacy_occurrences.get(digest, 0)
                legacy_occurrences[digest] = occurrence + 1
                key = f"legacy:{digest}:{occurrence}"
            row_key = (int(user_id), "submissions", key)
            payload = dumps(item)
            if row_key in rows and rows[row_key] != payload:
                raise ValueError("Conflicting records for one submission ID; no history overwritten.")
            rows[row_key] = payload
    return rows


def event_rows(guild):
    return {(kind, key): dumps(value) for kind in GUILD_HISTORY
            for key, value in guild.get(kind, {}).items()}


class PostgresStore:
    def __init__(self, url: str):
        if not url:
            raise ValueError("Set DATABASE_URL in .env beside bot.py.")
        self._url = url
        self.pool = None

    async def open(self):
        import asyncpg
        self.pool = await asyncpg.create_pool(
            dsn=self._url, min_size=1, max_size=2, statement_cache_size=0,
            timeout=15, command_timeout=20,
        )
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await self._initialize(connection)

    async def _initialize(self, connection):
        """Atomic backup/migration. A failed reconciliation rolls the whole change back."""
        await connection.execute("SELECT pg_advisory_xact_lock(781936284)")
        existing = await connection.fetchval("SELECT to_regclass('public.lc_arena_meta')")
        version = None
        if existing:
            version = await connection.fetchval("SELECT value FROM public.lc_arena_meta WHERE key='schema_version'")
            if version not in ("2", SCHEMA_VERSION):
                raise ValueError("Unsupported schema version; refusing to overwrite records.")
        elif await connection.fetchval(
            "SELECT to_regclass('public.lc_arena_guilds') IS NOT NULL OR "
            "to_regclass('public.lc_arena_players') IS NOT NULL"
        ):
            raise ValueError("Existing tables have no schema marker; refusing to overwrite records.")
        await connection.execute(SCHEMA)
        if version == "2":
            for kind, table, key in (
                ("v2_meta", "lc_arena_meta", "source.key"),
                ("v2_guild", "lc_arena_guilds", "source.guild_id::text"),
                ("v2_player", "lc_arena_players", "source.guild_id::text || ':' || source.user_id::text"),
            ):
                await connection.execute(
                    f"INSERT INTO public.lc_tracker_backups(kind,key,payload) "
                    f"SELECT $1,{key},to_jsonb(source) FROM public.{table} source "
                    "ON CONFLICT(kind,key) DO NOTHING", kind
                )
                valid = await connection.fetchval(
                    f"SELECT NOT EXISTS(SELECT 1 FROM public.{table} source WHERE NOT EXISTS "
                    f"(SELECT 1 FROM public.lc_tracker_backups backup WHERE backup.kind=$1 "
                    f"AND backup.key=({key}) AND backup.payload=to_jsonb(source)))", kind
                )
                if not valid:
                    raise ValueError("Pre-migration backup reconciliation failed; migration cancelled.")
            # Original rows remain intact. Their daily buckets migrate to auxiliary rows
            # on first edit; reads merge both sources and edits forbid history removal.
            await connection.execute(
                "INSERT INTO public.lc_arena_meta(key,value) VALUES('v3_backup_verified','true') "
                "ON CONFLICT(key) DO NOTHING"
            )
        # v2 CHECKs capped snapshots and disallowed detailed history. Backup has
        # already been verified before lifting these obsolete size limits.
        await connection.execute("ALTER TABLE public.lc_arena_players DROP CONSTRAINT IF EXISTS lc_arena_players_state_check")
        await connection.execute("ALTER TABLE public.lc_arena_guilds DROP CONSTRAINT IF EXISTS lc_arena_guilds_settings_check")
        await connection.execute(
            "INSERT INTO public.lc_arena_meta(key,value) VALUES('schema_version',$1) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", SCHEMA_VERSION
        )

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def _read(self, connection, guild_id: int, *, lock: bool = False, snapshots=None,
                    scheduler_now=None) -> dict:
        suffix = " FOR UPDATE" if lock else ""
        raw = await connection.fetchval("SELECT settings FROM public.lc_arena_guilds WHERE guild_id=$1" + suffix, guild_id)
        if snapshots is not None:
            snapshots["settings"] = dumps(decode(raw)) if raw is not None else None
            snapshots["players"] = {}
        guild = new_guild()
        if raw is not None:
            guild.update(decode(raw))
        guild["members"] = {}
        for kind in GUILD_HISTORY:
            guild.setdefault(kind, {})
        rows = await connection.fetch("SELECT user_id,state FROM public.lc_arena_players WHERE guild_id=$1" + suffix, guild_id)
        for row in rows:
            old = decode(row["state"])
            if snapshots is not None:
                snapshots["players"][str(row["user_id"])] = dumps(old)
            today = challenge_day(scheduler_now, old.get("timezone", guild["timezone"])) if scheduler_now else None
            guild["members"][str(row["user_id"])] = upgrade_member(old, today)
        since = None
        pending_dates = []
        if scheduler_now is not None:
            boundaries = []
            for member in guild["members"].values():
                today = challenge_day(scheduler_now, member["timezone"])
                cursor = member.get("roster_cursor")
                start = date.fromisoformat(cursor) - timedelta(days=1) if cursor else date.fromisoformat(member["roster_start"])
                boundaries.append(min(start, today - timedelta(days=max(1, today.weekday()))).isoformat())
            since = min(boundaries, default=(scheduler_now.date() - timedelta(days=1)).isoformat())
            # Load old undelivered reports as well as current days. Completed old
            # roster and report rows are omitted TOGETHER, never replayed as missing.
            pending_dates = [row["key"] for row in await connection.fetch(
                "SELECT event.key FROM public.lc_tracker_events event WHERE event.guild_id=$1 "
                "AND event.kind='roster' AND NOT EXISTS (SELECT 1 FROM public.lc_tracker_events report "
                "WHERE report.guild_id=event.guild_id AND report.kind='reports' AND report.key=event.key "
                "AND report.payload->>'complete'='true')", guild_id)]
            # Inline data from an older snapshot can contain an unfinished roster.
            pending_dates.extend(key for key in guild["roster"] if not guild["reports"].get(key, {}).get("complete"))
            pending_dates = sorted(set(pending_dates))
            for member in guild["members"].values():
                member["recent"] = {key: value for key, value in member["recent"].items()
                                    if key >= since or key in pending_dates}
                member["submissions"] = [entry for entry in member["submissions"]
                    if entry.get("date", entry.get("day", "")) >= since
                    or entry.get("date", entry.get("day", "")) in pending_dates]
            for kind in ("quests", "roster", "reports"):
                guild[kind] = {key: value for key, value in guild[kind].items() if key >= since or key in pending_dates}
            guild["reminders"] = {key: value for key, value in guild["reminders"].items()
                if value.get("status", "pending") not in {"sent", "cancelled"} or value.get("day", "") >= since}
        history_query = "SELECT user_id,kind,key,payload FROM public.lc_tracker_history WHERE guild_id=$1"
        history_args = [guild_id]
        if since is not None:
            history_query += (" AND ((kind='recent' AND (key >= $2 OR key=ANY($3::text[]))) OR "
                              "(kind='submissions' AND (coalesce(payload->>'date',payload->>'day') >= $2 "
                              "OR coalesce(payload->>'date',payload->>'day')=ANY($3::text[]))))")
            history_args += [since, pending_dates]
        for row in await connection.fetch(history_query, *history_args):
            member = guild["members"].get(str(row["user_id"]))
            if member is None:
                raise ValueError("Orphaned tracking history; refusing to hide records.")
            if row["kind"] == "recent":
                member["recent"][row["key"]] = decode(row["payload"])
            elif row["kind"] == "submissions":
                member["submissions"].append(decode(row["payload"]))
        for member in guild["members"].values():
            member["submissions"].sort(key=lambda entry: (entry.get("date", entry.get("day", "")), str(entry.get("submission_id", ""))))
        events_query = "SELECT kind,key,payload FROM public.lc_tracker_events WHERE guild_id=$1"
        event_args = [guild_id]
        if since is not None:
            events_query += (" AND ((kind NOT IN ('quests','roster','reports','reminders')) "
                             "OR (kind='reminders' AND (coalesce(payload->>'status','pending') NOT IN ('sent','cancelled') "
                             "OR payload->>'day' >= $2)) "
                             "OR (kind IN ('quests','roster','reports') AND (key >= $2 OR key=ANY($3::text[]))))")
            event_args += [since, pending_dates]
        for row in await connection.fetch(events_query, *event_args):
            guild.setdefault(row["kind"], {})[row["key"]] = decode(row["payload"])
        return guild

    async def scheduler_snapshot(self, guild_id: int, now) -> dict:
        """Hydrate scheduling windows and undelivered old reports, retaining all DB history."""
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await self._read(connection, guild_id, scheduler_now=now)

    @asynccontextmanager
    async def scheduler_edit(self, guild_id: int, now):
        """Write loaded/new scheduling rows only; unseen historical rows stay untouched."""
        async with self._edit(guild_id, scheduler_now=now) as guild:
            yield guild

    async def guild(self, guild_id: int) -> dict:
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await self._read(connection, guild_id)

    async def guild_ids(self) -> list[int]:
        async with self.pool.acquire() as connection:
            return [row["guild_id"] for row in await connection.fetch("SELECT guild_id FROM public.lc_arena_guilds")]

    @asynccontextmanager
    async def edit(self, guild_id: int):
        async with self._edit(guild_id) as guild:
            yield guild

    @asynccontextmanager
    async def _edit(self, guild_id: int, *, scheduler_now=None):
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", guild_id)
                snapshots = {}
                guild = await self._read(connection, guild_id, lock=True, snapshots=snapshots, scheduler_now=scheduler_now)
                before_settings = snapshots["settings"]
                before = snapshots["players"]
                inline_members = {key for key, value in before.items()
                                  if any(field in decode(value) for field in MEMBER_HISTORY)}
                before_history, before_events = history_rows(guild), event_rows(guild)
                exists = await connection.fetchval("SELECT EXISTS(SELECT 1 FROM public.lc_arena_guilds WHERE guild_id=$1)", guild_id)
                yield guild
                settings = encode_settings(guild)
                players = {key: encode_player(member) for key, member in guild["members"].items()}
                history, events = history_rows(guild), event_rows(guild)
                if not set(before) <= set(players):
                    raise ValueError("Removing player records is not supported.")
                if not set(before_history) <= set(history) or not set(before_events) <= set(events):
                    raise ValueError("Removing LeetCode history is not supported.")
                for key, old_payload in before_history.items():
                    if key[1] == "submissions" and history[key] != old_payload:
                        raise ValueError("Changing a recorded submission is not supported.")
                if scheduler_now is not None and any(
                    key[1] == "submissions" and before_history.get(key) != payload
                    for key, payload in history.items()
                ):
                    # A partial view cannot prove that a new ID is absent from
                    # older unloaded rows. Only full submission transactions may
                    # add records, so an accidental scoped upsert cannot replace one.
                    raise ValueError("Scheduler edits cannot add submission records; use a full tracking transaction.")
                if not exists or settings != before_settings:
                    await connection.execute(
                        "INSERT INTO public.lc_arena_guilds(guild_id,settings) VALUES($1,$2::jsonb) "
                        "ON CONFLICT(guild_id) DO UPDATE SET settings=EXCLUDED.settings", guild_id, settings)
                for key, value in players.items():
                    if before.get(key) != value:
                        await connection.execute(
                            "INSERT INTO public.lc_arena_players(guild_id,user_id,state) VALUES($1,$2,$3::jsonb) "
                            "ON CONFLICT(guild_id,user_id) DO UPDATE SET state=EXCLUDED.state", guild_id, int(key), value)
                # A scoped edit may not hydrate every inline v2 entry. Copy ALL
                # original inline entries before stripping the old JSON snapshot.
                inline_history = history_rows({"members": {key: decode(before[key]) for key in inline_members}})
                inline_history.update(history)
                original_events = event_rows(decode(before_settings)) if before_settings is not None else {}
                original_events.update(events)
                for (user_id, kind, key), payload in inline_history.items():
                    if before_history.get((user_id, kind, key)) != payload or str(user_id) in inline_members:
                        await connection.execute(
                            "INSERT INTO public.lc_tracker_history(guild_id,user_id,kind,key,payload) VALUES($1,$2,$3,$4,$5::jsonb) "
                            "ON CONFLICT(guild_id,user_id,kind,key) DO UPDATE SET payload=EXCLUDED.payload", guild_id, user_id, kind, key, payload)
                for (kind, key), payload in original_events.items():
                    if before_events.get((kind, key)) != payload or (before_settings is not None and kind in decode(before_settings)):
                        await connection.execute(
                            "INSERT INTO public.lc_tracker_events(guild_id,kind,key,payload) VALUES($1,$2,$3,$4::jsonb) "
                            "ON CONFLICT(guild_id,kind,key) DO UPDATE SET payload=EXCLUDED.payload", guild_id, kind, key, payload)

    async def import_json(self, path: Path) -> tuple[int, int]:
        if not path.exists():
            return 0, 0
        raw = path.read_bytes()
        source = json.loads(raw)
        # The raw source archive commits independently, even if overlap blocks import.
        fingerprint = hashlib.sha256(raw).hexdigest()
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO public.lc_tracker_backups(kind,key,payload) VALUES('legacy_json',$1,$2::jsonb) "
                    "ON CONFLICT(kind,key) DO NOTHING", fingerprint, dumps(source))
        converted = migrate_legacy(source, utcnow())
        imported_guilds = imported_players = 0
        for key, legacy in converted.items():
            async with self.edit(int(key)) as current:
                if current["legacy_imported"]:
                    continue
                if current["members"]:
                    raise ValueError("Legacy source archived, but PostgreSQL players overlap; reconcile before import. No records overwritten.")
                current.clear()
                current.update(legacy)
                imported_guilds += 1
                imported_players += len(legacy["members"])
        return imported_guilds, imported_players

    async def backup(self, path: Path):
        """Export a consistent full bot backup without overwriting an existing file."""
        tables = ("lc_arena_meta", "lc_arena_guilds", "lc_arena_players", "lc_tracker_history", "lc_tracker_events", "lc_tracker_backups")
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                data = {table: [decode(row["payload"]) for row in await connection.fetch(
                    f"SELECT to_jsonb(source) AS payload FROM public.{table} source")] for table in tables}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump({"schema_version": SCHEMA_VERSION, "tables": data}, target, indent=2)
        return {table: len(rows) for table, rows in data.items()}

    async def export_v2(self, path: Path):
        """Generate a baseline restore script for a separate, empty PostgreSQL DB.

        Later v3 activity remains in the current database; take a full --backup
        before rollback. No database rows are changed by this export.
        """
        async with self.pool.acquire() as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                rows = await connection.fetch(
                    "SELECT kind,key,payload FROM public.lc_tracker_backups "
                    "WHERE kind IN ('v2_meta','v2_guild','v2_player')")
        originals = {kind: [] for kind in ("v2_meta", "v2_guild", "v2_player")}
        for row in rows:
            originals[row["kind"]].append(decode(row["payload"]))
        if not any(row.get("key") == "schema_version" and row.get("value") == "2"
                   for row in originals["v2_meta"]):
            raise ValueError("No verified original v2 schema backup is available.")
        def literal(value):
            return "'" + str(value).replace("'", "''") + "'"
        statements = [
            "-- Original v2 baseline ONLY. Later activity remains in the v3 database.",
            "-- Run with psql -v ON_ERROR_STOP=1 against a SEPARATE EMPTY database.",
            "BEGIN;", "SET LOCAL standard_conforming_strings=on;",
            SCHEMA.split("CREATE TABLE IF NOT EXISTS public.lc_tracker_history")[0],
            "DO $restore$ BEGIN IF EXISTS(SELECT 1 FROM public.lc_arena_meta) "
            "OR EXISTS(SELECT 1 FROM public.lc_arena_guilds) "
            "OR EXISTS(SELECT 1 FROM public.lc_arena_players) THEN "
            "RAISE EXCEPTION 'Restore requires empty original tables; no records overwritten'; "
            "END IF; END $restore$;",
        ]
        for row in originals["v2_guild"]:
            statements.append("INSERT INTO public.lc_arena_guilds(guild_id,settings) VALUES ("
                              + str(int(row["guild_id"])) + "," + literal(dumps(row["settings"])) + "::jsonb);")
        for row in originals["v2_player"]:
            statements.append("INSERT INTO public.lc_arena_players(guild_id,user_id,state) VALUES ("
                              + str(int(row["guild_id"])) + "," + str(int(row["user_id"])) + ","
                              + literal(dumps(row["state"])) + "::jsonb);")
        for row in originals["v2_meta"]:
            statements.append("INSERT INTO public.lc_arena_meta(key,value) VALUES ("
                              + literal(row["key"]) + "," + literal(row["value"]) + ");")
        statements.append("COMMIT;")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write("\n".join(statements) + "\n")
        return {kind: len(values) for kind, values in originals.items()}

    async def usage(self) -> dict:
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT pg_database_size(current_database()) AS database_bytes, "
                "(SELECT coalesce(sum(pg_total_relation_size(oid)),0)::bigint FROM pg_class "
                "WHERE relnamespace='public'::regnamespace AND relname IN "
                "('lc_arena_meta','lc_arena_guilds','lc_arena_players','lc_tracker_history','lc_tracker_events','lc_tracker_backups')) AS bot_bytes, "
                "(SELECT count(*) FROM public.lc_arena_players) AS players")
            return dict(row)


async def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().with_name(".env"))
    parser = argparse.ArgumentParser(description="Initialize/migrate PostgreSQL without deleting tracking history.")
    parser.add_argument("--import-json", type=Path)
    parser.add_argument("--backup", type=Path, help="Export consistent bot tables to a new private JSON file")
    parser.add_argument("--export-v2", type=Path, help="Write original v2 baseline SQL for a separate empty database")
    args = parser.parse_args()
    store = PostgresStore(os.getenv("DATABASE_URL", ""))
    try:
        await store.open()
        if args.backup:
            print("Backup row counts:", await store.backup(args.backup))
        if args.export_v2:
            print("Original v2 baseline rows:", await store.export_v2(args.export_v2))
            print("Restore only into a separate empty database. Newer activity remains in v3; keep its full backup.")
        if args.import_json:
            guilds, players = await store.import_json(args.import_json)
            print(f"Imported {players} players across {guilds} guilds; source archived intact.")
        usage = await store.usage()
        print(f"Database: {usage['database_bytes'] / 1024**2:.2f} MiB; bot tables: {usage['bot_bytes'] / 1024**2:.2f} MiB; players: {usage['players']}.")
    finally:
        await store.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"Database setup failed ({type(exc).__name__}). Check connectivity/schema permissions; no history intentionally removed.")
        raise SystemExit(1)
