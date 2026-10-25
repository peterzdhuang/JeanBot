"""Transactional storage contracts; no live database or credentials required."""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from storage import PostgresStore, encode_player, encode_settings
from tracker import enroll, new_guild, new_member


class FakeConnection:
    def __init__(self):
        self.guilds, self.players, self.history, self.events, self.backups = {}, {}, {}, {}, {}
        self.meta = {}
        self.queries = []
        self.fail_player_write = False
        self.fail_history_write = False
        self.fail_backup_reconciliation = False

    @asynccontextmanager
    async def transaction(self, **kwargs):
        attributes = ('guilds', 'players', 'history', 'events', 'backups', 'meta')
        before = {key: copy.deepcopy(getattr(self, key)) for key in attributes}
        try:
            yield self
        except BaseException:
            for key, value in before.items():
                setattr(self, key, value)
            raise

    async def execute(self, query, *args):
        self.queries.append((query, args))
        canonical = lambda value: json.dumps(json.loads(value), sort_keys=True)
        if query.startswith("INSERT INTO public.lc_arena_guilds"):
            self.guilds[args[0]] = canonical(args[1])
        elif query.startswith("INSERT INTO public.lc_arena_players"):
            if self.fail_player_write:
                raise RuntimeError("injected player write failure")
            self.players[(args[0], args[1])] = canonical(args[2])
        elif query.startswith("INSERT INTO public.lc_tracker_history"):
            if self.fail_history_write:
                raise RuntimeError("injected history write failure")
            self.history[args[:4]] = canonical(args[4])
        elif query.startswith("INSERT INTO public.lc_tracker_events"):
            self.events[args[:3]] = canonical(args[3])
        elif query.startswith("INSERT INTO public.lc_tracker_backups"):
            if "'legacy_json'" in query:
                self.backups.setdefault(('legacy_json', args[0]), args[1])
            else:
                kind = args[0]
                if kind == 'v2_meta':
                    rows = [(key, {'key': key, 'value': value}) for key, value in self.meta.items()]
                elif kind == 'v2_guild':
                    rows = [(str(key), {'guild_id': key, 'settings': json.loads(value)}) for key, value in self.guilds.items()]
                else:
                    rows = [(f'{guild}:{user}', {'guild_id': guild, 'user_id': user, 'state': json.loads(value)})
                            for (guild, user), value in self.players.items()]
                for key, payload in rows:
                    self.backups.setdefault((kind, key), json.dumps(payload))
        elif query.startswith("INSERT INTO public.lc_arena_meta"):
            if 'schema_version' in query:
                self.meta['schema_version'] = args[0]
            elif 'v3_backup_verified' in query:
                self.meta['v3_backup_verified'] = 'true'

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        if "SELECT settings FROM" in query:
            return self.guilds.get(args[0])
        if "SELECT EXISTS" in query:
            return args[0] in self.guilds
        if "SELECT value FROM" in query:
            return self.meta.get('schema_version')
        if "SELECT NOT EXISTS" in query:
            return not self.fail_backup_reconciliation
        if "SELECT to_regclass('public.lc_arena_meta')" in query:
            return 'lc_arena_meta' if self.meta else None
        if "to_regclass" in query:
            return bool(self.guilds or self.players)
        raise AssertionError(f"Unexpected fake query: {query}")

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        if "SELECT user_id,state" in query:
            return [{'user_id': user, 'state': value} for (guild, user), value in self.players.items() if guild == args[0]]
        if "FROM public.lc_tracker_history" in query:
            rows = [{'user_id': user, 'kind': kind, 'key': key, 'payload': value}
                    for (guild, user, kind, key), value in self.history.items() if guild == args[0]]
            if len(args) > 1:
                since, pending = args[1:]
                rows = [row for row in rows if (
                    row['key'] if row['kind'] == 'recent' else json.loads(row['payload']).get('date', json.loads(row['payload']).get('day', ''))
                ) >= since or (
                    row['key'] if row['kind'] == 'recent' else json.loads(row['payload']).get('date', json.loads(row['payload']).get('day', ''))
                ) in pending]
            return rows
        if "FROM public.lc_tracker_events event" in query:
            return [{'key': key} for (guild, kind, key), value in self.events.items()
                    if guild == args[0] and kind == 'roster'
                    and not json.loads(self.events.get((guild, 'reports', key), '{}')).get('complete')]
        if "FROM public.lc_tracker_events" in query:
            rows = [{'kind': kind, 'key': key, 'payload': value}
                    for (guild, kind, key), value in self.events.items() if guild == args[0]]
            if len(args) > 1:
                since, pending = args[1:]
                rows = [row for row in rows if (
                    row['kind'] not in ('quests', 'roster', 'reports', 'reminders')
                    or row['kind'] == 'reminders' and (
                        json.loads(row['payload']).get('status', 'pending') not in ('sent', 'cancelled')
                        or json.loads(row['payload']).get('day', '') >= since)
                    or row['kind'] in ('quests', 'roster', 'reports') and (row['key'] >= since or row['key'] in pending))]
            return rows
        if "FROM public.lc_tracker_backups" in query:
            return [{'kind': kind, 'key': key, 'payload': value}
                    for (kind, key), value in self.backups.items() if kind in ('v2_meta', 'v2_guild', 'v2_player')]
        if "SELECT guild_id" in query:
            return [{'guild_id': key} for key in self.guilds]
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
        self.store = PostgresStore('postgresql://test:fake@localhost/test')
        self.store.pool = FakePool()
        self.connection = self.store.pool.connection
        self.day = date(2026, 10, 10)

    async def seed(self):
        async with self.store.edit(7) as data:
            enroll(data, 1, self.day, 'PST')

    async def scheduler_fixture(self):
        await self.seed()
        async with self.store.edit(7) as data:
            member = data['members']['1']
            member['roster_start'] = '2020-01-01'
            member['roster_cursor'] = '2026-10-10'
            member['recent'] = {
                key: {'counts': [1, 0, 0], 'quests': 0}
                for key in ('2020-01-01', '2020-01-02', '2026-10-09', '2026-10-10')}
            member['submissions'] = [{'date': key, 'submission_id': str(index), 'url': 'preserve'}
                for index, key in enumerate(member['recent'], 1)]
            for key in member['recent']:
                data['quests'][key] = {'slug': 'old-quest'}
                data['roster'][key] = {'1': {'closed': True}}
                data['reports'][key] = {'complete': key != '2020-01-02'}
            data['buddies']['1:2'] = {'inviter': '1', 'invitee': '2', 'status': 'accepted'}
            data['reminders']['pending-delivery'] = {'status': 'pending'}

    async def test_scheduler_scope_keeps_recent_and_old_unsent_reports(self):
        await self.scheduler_fixture()
        now = datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
        scoped = await self.store.scheduler_snapshot(7, now)
        self.assertEqual(set(scoped['members']['1']['recent']), {'2020-01-02', '2026-10-09', '2026-10-10'})
        self.assertEqual(set(scoped['roster']), {'2020-01-02', '2026-10-09', '2026-10-10'})
        self.assertNotIn('2020-01-01', scoped['reports'])
        self.assertEqual(len(scoped['members']['1']['submissions']), 3)
        self.assertIn('1:2', scoped['buddies'])
        self.assertIn('pending-delivery', scoped['reminders'])
        self.assertTrue(any('key >= $2' in query for query, _ in self.connection.queries))
        self.assertEqual(len((await self.store.guild(7))['members']['1']['submissions']), 4)

    async def test_scheduler_partial_edit_never_prunes_unloaded_history(self):
        await self.scheduler_fixture()
        original_history = copy.deepcopy(self.connection.history)
        old_roster = self.connection.events[(7, 'roster', '2020-01-01')]
        now = datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
        async with self.store.scheduler_edit(7, now) as data:
            self.assertNotIn('2020-01-01', data['members']['1']['recent'])
            data['members']['1']['reminders'] = False
            data['reports']['2020-01-02']['complete'] = True
            data['reminders']['pending-delivery']['status'] = 'sent'
        self.assertEqual(self.connection.history, original_history)
        self.assertEqual(self.connection.events[(7, 'roster', '2020-01-01')], old_roster)
        full = await self.store.guild(7)
        self.assertEqual(len(full['members']['1']['submissions']), 4)
        self.assertTrue(full['reports']['2020-01-02']['complete'])
        self.assertFalse(full['members']['1']['reminders'])
        self.assertIn('2020-01-01', full['roster'])

    async def test_scheduler_loads_weekly_goal_window_and_pending_reminder_outbox(self):
        await self.scheduler_fixture()
        async with self.store.edit(7) as data:
            data['members']['1']['recent']['2026-10-05'] = {'counts': [0, 1, 0], 'quests': 0}
            for status in ('pending', 'attempting', 'uncertain', 'sent', 'cancelled'):
                data['reminders']['old-' + status] = {'day': '2020-01-01', 'status': status}
                data['reminders']['new-' + status] = {'day': '2026-10-10', 'status': status}
        now = datetime(2026, 10, 10, 18, tzinfo=timezone.utc)
        scoped = await self.store.scheduler_snapshot(7, now)
        self.assertIn('2026-10-05', scoped['members']['1']['recent'])
        for status in ('pending', 'attempting', 'uncertain'):
            self.assertIn('old-' + status, scoped['reminders'])
        for status in ('sent', 'cancelled'):
            self.assertNotIn('old-' + status, scoped['reminders'])
            self.assertIn('new-' + status, scoped['reminders'])
        async with self.store.scheduler_edit(7, now):
            pass
        full = await self.store.guild(7)
        self.assertIn('old-sent', full['reminders'])
        self.assertIn('old-cancelled', full['reminders'])

    async def test_scheduler_cannot_overwrite_unloaded_submission_by_id(self):
        await self.scheduler_fixture()
        before = copy.deepcopy(self.connection.history)
        with self.assertRaisesRegex(ValueError, 'cannot add submission'):
            async with self.store.scheduler_edit(7, datetime(2026, 10, 10, 18, tzinfo=timezone.utc)) as data:
                self.assertTrue(all(entry['submission_id'] != '1' for entry in data['members']['1']['submissions']))
                data['members']['1']['submissions'].append({'date': '2026-10-10', 'submission_id': '1', 'url': 'replacement'})
        self.assertEqual(self.connection.history, before)

    async def test_scheduler_initial_cursor_absence_loads_roster_start(self):
        await self.scheduler_fixture()
        async with self.store.edit(7) as data:
            del data['members']['1']['roster_cursor']
        scoped = await self.store.scheduler_snapshot(7, datetime(2026, 10, 10, 18, tzinfo=timezone.utc))
        self.assertEqual(len(scoped['members']['1']['submissions']), 4)
        self.assertIn('2020-01-01', scoped['reports'])
        self.assertTrue(scoped['reports']['2020-01-01']['complete'])

    async def test_scheduler_stripping_inline_v2_state_keeps_unloaded_history(self):
        await self.scheduler_fixture()
        full = await self.store.guild(7)
        # Emulate an old inline snapshot while normalized storage has no rows yet.
        self.connection.players[(7, 1)] = json.dumps(full['members']['1'])
        self.connection.guilds[7] = json.dumps({key: value for key, value in full.items() if key != 'members'})
        self.connection.history.clear()
        self.connection.events.clear()
        async with self.store.scheduler_edit(7, datetime(2026, 10, 10, 18, tzinfo=timezone.utc)) as scoped:
            self.assertNotIn('2020-01-01', scoped['members']['1']['recent'])
            scoped['members']['1']['reminders'] = False
        restored = await self.store.guild(7)
        self.assertEqual(restored['members']['1']['submissions'], full['members']['1']['submissions'])
        self.assertEqual(restored['members']['1']['recent'], full['members']['1']['recent'])
        self.assertEqual(restored['roster'], full['roster'])
        self.assertEqual(restored['reports'], full['reports'])
        self.assertEqual(restored['buddies'], full['buddies'])

    async def test_save_reload_and_archive_unknown_fields(self):
        await self.seed()
        async with self.store.edit(7) as data:
            data['custom_legacy_setting'] = {'preserve': True}
            data['members']['1']['unrecognized_old_state'] = {'keep': [1, 2, 3]}
            data['members']['1']['pet'] = {'old_species': 'retain exactly'}
            data['members']['1']['reminders'] = False
        saved = await self.store.guild(7)
        self.assertEqual(saved['custom_legacy_setting'], {'preserve': True})
        self.assertEqual(saved['members']['1']['unrecognized_old_state'], {'keep': [1, 2, 3]})
        self.assertEqual(saved['members']['1']['pet'], {'old_species': 'retain exactly'})
        self.assertFalse(saved['members']['1']['reminders'])
        self.assertEqual(saved['members']['1']['timezone'], 'America/Los_Angeles')

    async def test_unbounded_history_survives_separate_rows(self):
        await self.seed()
        async with self.store.edit(7) as data:
            member = data['members']['1']
            member['recent'] = {f'2020-{month:02}-{day:02}': {'counts': [1, 0, 0], 'quests': 0}
                                for month in range(1, 13) for day in range(1, 29)}
            member['submissions'] = [{'submission_id': str(index), 'date': '2020-01-01',
                                      'url': f'https://leetcode.com/submissions/detail/{index}/'} for index in range(100)]
            data['quests']['2020-01-01'] = {'slug': 'preserved'}
        saved = await self.store.guild(7)
        self.assertEqual(len(saved['members']['1']['recent']), 336)
        self.assertEqual(len(saved['members']['1']['submissions']), 100)
        self.assertNotIn('recent', json.loads(self.connection.players[(7, 1)]))
        self.assertNotIn('submissions', json.loads(self.connection.players[(7, 1)]))
        self.assertNotIn('quests', json.loads(self.connection.guilds[7]))
        self.assertEqual(saved['quests']['2020-01-01']['slug'], 'preserved')

    async def test_identical_legacy_records_are_not_collapsed(self):
        await self.seed()
        entry = {'legacy': True, 'date': '2020-01-01', 'url': 'same-old-problem-link'}
        async with self.store.edit(7) as data:
            data['members']['1']['submissions'] = [copy.deepcopy(entry), copy.deepcopy(entry)]
        saved = (await self.store.guild(7))['members']['1']['submissions']
        self.assertEqual(saved, [entry, entry])
        async with self.store.edit(7):
            pass
        self.assertEqual((await self.store.guild(7))['members']['1']['submissions'], [entry, entry])

    async def test_noop_does_not_write_empty_history(self):
        await self.seed()
        self.connection.queries.clear()
        async with self.store.edit(7):
            pass
        self.assertFalse(any(query.startswith('INSERT') for query, _ in self.connection.queries))

    async def test_history_removal_and_submission_mutation_refused(self):
        await self.seed()
        async with self.store.edit(7) as data:
            data['members']['1']['submissions'] = [{'submission_id': '12', 'url': 'original'}]
        for action in ('remove', 'change'):
            with self.assertRaises(ValueError):
                async with self.store.edit(7) as data:
                    if action == 'remove':
                        data['members']['1']['submissions'].clear()
                    else:
                        data['members']['1']['submissions'][0]['url'] = 'changed'
        self.assertEqual((await self.store.guild(7))['members']['1']['submissions'][0]['url'], 'original')

    async def test_history_write_failure_rolls_back_counts(self):
        await self.seed()
        self.connection.fail_history_write = True
        with self.assertRaises(RuntimeError):
            async with self.store.edit(7) as data:
                member = data['members']['1']
                member['solved'] = 1
                member['submissions'].append({'submission_id': '42', 'url': 'test'})
        self.assertEqual((await self.store.guild(7))['members']['1']['solved'], 0)
        self.assertEqual(self.connection.history, {})

    async def test_v2_backup_before_migration_preserves_originals(self):
        member = new_member(self.day, 'PST')
        member['recent'] = {'2000-01-01': {'counts': [2, 3, 4], 'quests': 7}}
        member['legacy_extra'] = 'unchanged'
        original = json.dumps(member)
        self.connection.players[(7, 1)] = original
        self.connection.guilds[7] = json.dumps({'timezone': 'America/Vancouver', 'reminders_enabled': False})
        self.connection.meta['schema_version'] = '2'
        async with self.connection.transaction():
            await self.store._initialize(self.connection)
        self.assertEqual(self.connection.players[(7, 1)], original)
        self.assertEqual(json.loads(self.connection.backups[('v2_player', '7:1')])['state'], member)
        self.assertEqual(self.connection.meta['schema_version'], '3')
        async with self.store.edit(7) as data:
            data['members']['1']['solved'] += 1
        reloaded = (await self.store.guild(7))['members']['1']
        self.assertEqual(reloaded['recent'], member['recent'])
        self.assertEqual(reloaded['legacy_extra'], 'unchanged')

    async def test_noop_v2_edit_copies_inline_history_and_persists_upgrade(self):
        member = new_member(self.day, 'UTC')
        for field in ('coverage_start', 'quest_start', 'periods', 'unknown_count'):
            member.pop(field, None)
        member['recent'] = {'2020-01-01': {'counts': [2, 0, 0], 'quests': 1}}
        member['submissions'] = [{'submission_id': '123', 'date': '2020-01-01', 'url': 'original'}]
        self.connection.players[(7, 1)] = json.dumps(member)
        self.connection.guilds[7] = json.dumps(new_guild() | {'members': {}})
        async with self.store.edit(7):
            pass
        persisted = json.loads(self.connection.players[(7, 1)])
        self.assertIn('coverage_start', persisted)
        self.assertNotIn('recent', persisted)
        self.assertNotIn('submissions', persisted)
        reloaded = (await self.store.guild(7))['members']['1']
        self.assertEqual(reloaded['recent'], member['recent'])
        self.assertEqual(reloaded['submissions'], member['submissions'])
        self.connection.queries.clear()
        async with self.store.edit(7):
            pass
        self.assertFalse(any(query.startswith('INSERT') for query, _ in self.connection.queries))

    async def test_export_v2_baseline_is_private_non_destructive_and_escaped(self):
        self.connection.meta['schema_version'] = '2'
        member = new_member(self.day, 'UTC')
        member['pet'] = {'name': "Duck's pet; DROP TABLE example; --"}
        self.connection.players[(7, 1)] = json.dumps(member)
        self.connection.guilds[7] = json.dumps({'timezone': 'UTC'})
        async with self.connection.transaction():
            await self.store._initialize(self.connection)
        before = copy.deepcopy(self.connection.players)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'restore.sql'
            counts = await self.store.export_v2(path)
            self.assertEqual(counts['v2_player'], 1)
            script = path.read_text()
            self.assertIn("Duck''s pet; DROP TABLE example; --", script)
            self.assertIn('Restore requires empty original tables', script)
            self.assertIn('COMMIT;', script)
            if os.name == 'posix':  # Windows ignores POSIX permission bits.
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                await self.store.export_v2(path)
        self.assertEqual(self.connection.players, before)

    async def test_failed_backup_reconciliation_rolls_back_version(self):
        self.connection.meta['schema_version'] = '2'
        self.connection.fail_backup_reconciliation = True
        with self.assertRaises(ValueError):
            async with self.connection.transaction():
                await self.store._initialize(self.connection)
        self.assertEqual(self.connection.meta['schema_version'], '2')
        self.assertEqual(self.connection.backups, {})

    async def test_import_archives_raw_and_preserves_labels(self):
        legacy = {'version': 1, 'guilds': {'7': {'timezone': 'UTC',
                  'members': {'1': {'joined': '2020-01-01', 'active': True}},
                  'submissions': [{'user_id': '1', 'date': '2020-01-01', 'slug': 'two-sum',
                                   'url': 'https://leetcode.com/problems/two-sum/', 'extra': 'retain'}]}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text(json.dumps(legacy))
            original = path.read_bytes()
            self.assertEqual(await self.store.import_json(path), (1, 1))
            self.assertEqual(await self.store.import_json(path), (0, 0))
            self.assertEqual(path.read_bytes(), original)
        saved = (await self.store.guild(7))['members']['1']
        self.assertEqual(saved['solved'], 1)
        self.assertIn('2020-01-01', saved['recent'])
        self.assertEqual(saved['submissions'][0]['raw']['extra'], 'retain')
        self.assertEqual(json.loads(next(value for (kind, _), value in self.connection.backups.items()
                                             if kind == 'legacy_json')), legacy)

    async def test_overlap_archives_source_but_does_not_overwrite(self):
        await self.seed()
        original = (await self.store.guild(7))['members']['1']
        legacy = {'version': 1, 'guilds': {'7': {'timezone': 'UTC',
                  'members': {'1': {'joined': '2020-01-01'}}, 'submissions': []}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            path.write_text(json.dumps(legacy))
            with self.assertRaisesRegex(ValueError, 'overlap'):
                await self.store.import_json(path)
        self.assertEqual((await self.store.guild(7))['members']['1'], original)
        self.assertTrue(any(kind == 'legacy_json' for kind, _ in self.connection.backups))

    async def test_concurrent_counts_are_serialized(self):
        await self.seed()
        async def increment():
            async with self.store.edit(7) as data:
                count = data['members']['1']['solved']
                await asyncio.sleep(0)
                data['members']['1']['solved'] = count + 1
        await asyncio.gather(*(increment() for _ in range(20)))
        self.assertEqual((await self.store.guild(7))['members']['1']['solved'], 20)

    async def test_player_removal_refused(self):
        await self.seed()
        with self.assertRaises(ValueError):
            async with self.store.edit(7) as data:
                del data['members']['1']
        self.assertIn('1', (await self.store.guild(7))['members'])

    def test_missing_url_is_clear(self):
        with self.assertRaisesRegex(ValueError, 'DATABASE_URL'):
            PostgresStore('')


if __name__ == '__main__':
    unittest.main()
