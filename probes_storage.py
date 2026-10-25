"""Offline, synthetic storage/scheduler probes; never reads .env or opens a socket.

Run: python3 probes_storage.py
Timings measure Python plus transactional fakes, NOT PostgreSQL/Discord latency.
The fake deep-copies tables per transaction; query/row/byte counts are more
portable evidence than its elapsed times. No runtime files are changed.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

from scheduler import process_guild as process_scheduler
from storage import PostgresStore, dumps, encode_player, encode_settings, event_rows, history_rows
from test_bot import FakeHTTPException, FakeResponse, command_namespace
from test_storage import FakeConnection, FakePool
from tracker import deadline, new_guild, new_member

NOW = datetime(2026, 10, 25, 18, tzinfo=timezone.utc)
TODAY = date(2026, 10, 25)


class MeteredConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.reset_meter()

    def reset_meter(self):
        self.queries.clear()
        self.rows_returned = 0
        self.payload_bytes_returned = 0
        self.transactions = 0

    def transaction(self, **kwargs):
        self.transactions += 1
        return super().transaction(**kwargs)

    async def fetch(self, query, *args):
        rows = await super().fetch(query, *args)
        self.rows_returned += len(rows)
        self.payload_bytes_returned += sum(len(json.dumps(row).encode()) for row in rows)
        return rows

    async def fetchval(self, query, *args):
        value = await super().fetchval(query, *args)
        if value is not None:
            self.payload_bytes_returned += len(json.dumps(value).encode())
        return value

    def result(self, elapsed):
        return {
            'elapsed_ms': round(elapsed * 1000, 2),
            'queries': len(self.queries),
            'transactions': self.transactions,
            'rows_returned': self.rows_returned,
            'payload_mib_returned': round(self.payload_bytes_returned / 1024**2, 3),
            'writes': sum(query.startswith(('INSERT', 'UPDATE', 'DELETE')) for query, _ in self.queries),
        }


def synthetic_store(members=1, days=1, *, pending=False):
    """One solve/day/member; direct seeding skips irrelevant setup write costs."""
    store = PostgresStore('postgresql://synthetic:unused@offline.invalid/probes')
    store.pool = FakePool()
    connection = store.pool.connection = MeteredConnection()
    guild = new_guild()
    guild['report_channel_id'] = 9
    start = TODAY - timedelta(days=days - 1)
    for user_id in range(1, members + 1):
        member = new_member(start, 'UTC')
        member['roster_start'] = start.isoformat()
        member['roster_cursor'] = TODAY.isoformat()
        member['solved'] = member['period_solved'] = member['completed_days'] = days - int(pending)
        member['counts'] = [0, days - int(pending), 0]
        member['last_solved'] = (TODAY - timedelta(days=int(pending))).isoformat() if days > int(pending) else None
        for index in range(days):
            day = start + timedelta(days=index)
            label = day.isoformat()
            done = not (pending and day == TODAY)
            if done:
                member['recent'][label] = {'counts': [0, 1, 0], 'quests': 0}
                submission_id = str(user_id * 1_000_000 + index)
                member['submissions'].append({'date': label, 'slug': f'probe-problem-{index}',
                    'submission_id': submission_id, 'difficulty': 'Medium',
                    'url': f'https://leetcode.com/problems/probe-problem-{index}/submissions/{submission_id}/',
                    'timezone': 'UTC', 'received_at': f'{label}T18:00:00+00:00'})
            guild['quests'].setdefault(label, {'slug': f'probe-problem-{index}', 'name': 'Synthetic quest',
                'difficulty': 'Medium', 'url': f'https://leetcode.com/problems/probe-problem-{index}/'})
            guild['roster'].setdefault(label, {})[str(user_id)] = {
                'timezone': 'UTC', 'deadline': deadline(day, 'UTC').isoformat(),
                'closed': day < TODAY, 'excused': False, 'count': int(done), 'quest': False,
                'links': [member['submissions'][-1]['url']] if done else []}
            if day < TODAY:
                guild['reports'][label] = {'complete': True}
        guild['members'][str(user_id)] = member
    connection.guilds[7] = encode_settings(guild)
    connection.players = {(7, int(user_id)): encode_player(member) for user_id, member in guild['members'].items()}
    connection.history = {(7, user_id, kind, key): payload
                          for (user_id, kind, key), payload in history_rows(guild).items()}
    connection.events = {(7, kind, key): payload for (kind, key), payload in event_rows(guild).items()}
    return store


async def measured(connection, operation):
    connection.reset_meter()
    started = time.perf_counter()
    await operation()
    return connection.result(time.perf_counter() - started)


async def scaling_probe(members, days):
    store = synthetic_store(members, days)
    connection = store.pool.connection
    ns = command_namespace()
    ns['utcnow'] = lambda: NOW
    channel = types.SimpleNamespace(send=AsyncMock(return_value=types.SimpleNamespace(id=123)))
    bot = types.SimpleNamespace(store=store, get_channel=lambda _: channel,
                               find_report_message=AsyncMock(return_value=None))

    async def member_edit():
        async with store.edit(7) as data:
            data['members']['1']['reminders'] = not data['members']['1']['reminders']

    result = {
        'members': members, 'history_days': days,
        'stored_history_rows': len(connection.history), 'stored_event_rows': len(connection.events),
        'guild_read': await measured(connection, lambda: store.guild(7)),
        'one_member_preference_edit': await measured(connection, member_edit),
        'scoped_scheduler_snapshot': await measured(connection, lambda: store.scheduler_snapshot(7, NOW)),
        'quiet_scheduler_tick': await measured(connection, lambda: process_scheduler(bot, 7, clock=lambda: NOW)),
        'discord_sends': channel.send.await_count,
    }
    quiet = result['quiet_scheduler_tick']
    assert quiet['transactions'] == 1, 'Quiet scheduler must not open transactions per member'
    assert quiet['queries'] <= 8, 'Quiet scheduler query count must be independent of member count'
    assert quiet['writes'] == 0 and result['discord_sends'] == 0
    assert quiet['payload_mib_returned'] < result['guild_read']['payload_mib_returned']
    result['bounded_quiet_scan_checks_passed'] = True
    return result


def interaction():
    return types.SimpleNamespace(guild_id=7,
        user=types.SimpleNamespace(id=1, display_name='Synthetic member',
                                  display_avatar=types.SimpleNamespace(url='https://example.invalid/avatar.png')),
        response=FakeResponse(), followup=types.SimpleNamespace(send=AsyncMock()))


async def commit_and_duplicate_probe():
    store = synthetic_store(1, 1, pending=True)
    ns = command_namespace()
    ns['utcnow'] = lambda: NOW
    ns['bot'] = types.SimpleNamespace(store=store, submit_lock=asyncio.Lock())
    # Keep this fault probe about commits/transport, not image rendering cost.
    ns['render_activity'] = lambda *args: b'not-a-real-image-used-by-mocked-transport'
    attempt = 'https://leetcode.com/problems/two-sum/submissions/987654321/'
    first = interaction()
    first.followup.send.side_effect = [FakeHTTPException('attachment unavailable'), FakeHTTPException('text unavailable')]
    await ns['submit'](first, attempt)
    committed = (await store.guild(7))['members']['1']
    restarted = command_namespace()
    restarted['utcnow'] = lambda: NOW
    restarted['bot'] = types.SimpleNamespace(store=store, submit_lock=asyncio.Lock())
    retried = interaction()
    await restarted['submit'](retried, attempt)
    after_retry = (await store.guild(7))['members']['1']

    failed_store = synthetic_store(1, 1, pending=True)
    failed_store.pool.connection.fail_player_write = True
    ns['bot'] = types.SimpleNamespace(store=failed_store, submit_lock=asyncio.Lock())
    failed = interaction()
    await ns['submit'](failed, attempt)
    rolled_back = (await failed_store.guild(7))['members']['1']
    return {
        'discord_attachment_and_text_failures': first.followup.send.await_count,
        'saved_solves_after_discord_failure': committed['solved'],
        'saved_submissions_after_discord_failure': len(committed['submissions']),
        'duplicate_after_new_namespace_error': retried.followup.send.call_args.args[0],
        'solves_after_retry': after_retry['solved'],
        'failed_db_commit_solves': rolled_back['solved'],
        'failed_db_commit_submissions': len(rolled_back['submissions']),
        'failed_db_commit_response': failed.followup.send.call_args.args[0],
    }


async def uncertain_reminder_probe(*, history_available=True):
    """Inject commit failure after a send; recover or defer rather than repeat."""
    store = synthetic_store(1, 1, pending=True)
    now = NOW.replace(hour=20, minute=1)
    connection = store.pool.connection
    messages = []

    async def send(text, **kwargs):
        message = types.SimpleNamespace(id=999 + len(messages), content=text,
                                        author=types.SimpleNamespace(id=77))
        messages.append(message)
        return message

    async def history(limit=None, after=None):
        if not history_available:
            raise FakeHTTPException('history unavailable')
        for message in reversed(messages):
            yield message

    channel = types.SimpleNamespace(send=AsyncMock(side_effect=send), history=history)
    bot = types.SimpleNamespace(store=store, user=types.SimpleNamespace(id=77), get_channel=lambda _: channel)
    original_execute = connection.execute
    fail_once = True

    async def fail_after_send(query, *args):
        nonlocal fail_once
        if query.startswith('INSERT INTO public.lc_arena_players') and channel.send.await_count and fail_once:
            fail_once = False
            raise RuntimeError('synthetic commit failure after Discord send')
        return await original_execute(query, *args)

    connection.execute = fail_after_send
    failed = False
    try:
        await process_scheduler(bot, 7, clock=lambda: now)
    except RuntimeError:
        failed = True
    after_failure = await store.guild(7)
    persisted_marks = copy.deepcopy(after_failure['members']['1']['reminder_marks'])
    await process_scheduler(bot, 7, clock=lambda: now)
    after_retry = await store.guild(7)
    statuses = [event['status'] for event in after_retry['reminders'].values()]
    result = {'injected_failure_raised': failed, 'persisted_marks_after_failed_commit': persisted_marks,
              'history_available': history_available,
              'discord_reminder_sends_after_retry': channel.send.await_count,
              'same_reminder_resent': channel.send.await_count > 1,
              'delivery_statuses_after_retry': statuses}
    assert failed and channel.send.await_count == 1
    assert statuses == (['sent'] if history_available else ['uncertain'])
    result['reconciliation_checks_passed'] = True
    return result


async def uncertain_report_probe():
    """A report sent before a failed commit is recovered from mock channel history."""
    store = synthetic_store(1, 2)
    connection = store.pool.connection
    previous_day = (TODAY - timedelta(days=1)).isoformat()
    connection.events[(7, 'reports', previous_day)] = '{}'
    ns = command_namespace()
    ns['utcnow'] = lambda: NOW
    messages = []
    bot = types.SimpleNamespace(store=store, user=types.SimpleNamespace(id=77))

    async def send(text, **kwargs):
        message = types.SimpleNamespace(id=9000 + len(messages), content=text,
                                        author=types.SimpleNamespace(id=77))
        messages.append(message)
        return message

    async def history(limit=None, after=None):
        for message in reversed(messages if limit is None else messages[-limit:]):
            yield message

    channel = types.SimpleNamespace(send=AsyncMock(side_effect=send), history=history)
    bot.get_channel = lambda _: channel
    bot.find_report_message = lambda channel, marker: ns['find_report_message'](bot, channel, marker)
    original_execute = connection.execute
    fail_once = True

    async def fail_after_send(query, *args):
        nonlocal fail_once
        if (query.startswith('INSERT INTO public.lc_tracker_events') and args[1] == 'reports'
                and messages and fail_once):
            fail_once = False
            raise RuntimeError('synthetic report commit failure after send')
        return await original_execute(query, *args)

    connection.execute = fail_after_send
    failed = False
    try:
        await process_scheduler(bot, 7, clock=lambda: NOW, report_builder=ns['report_pages'])
    except RuntimeError:
        failed = True
    after_failure = (await store.guild(7))['reports'][previous_day]
    await process_scheduler(bot, 7, clock=lambda: NOW, report_builder=ns['report_pages'])
    recovered = (await store.guild(7))['reports'][previous_day]
    assert failed and channel.send.await_count == 1 and recovered.get('complete')
    return {'injected_failure_raised': failed,
            'persisted_message_ids_after_failed_commit': after_failure.get('message_ids', []),
            'discord_report_sends_after_retry': channel.send.await_count,
            'recovered_message_ids': recovered.get('message_ids', []),
            'report_completed': recovered.get('complete', False),
            'reconciliation_checks_passed': True}


async def run_probes():
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        results = {
            'scope': 'Synthetic in-memory fakes only; no .env, sockets, Discord, or PostgreSQL connection.',
            'timing_limit': 'Includes fake whole-table deepcopy/rollback overhead; not a production latency estimate.',
            'scaling': [await scaling_probe(*size) for size in ((5, 30), (20, 90), (30, 180))],
            'failure_flow': await commit_and_duplicate_probe(),
            'uncertain_reminder_delivery': await uncertain_reminder_probe(),
            'uncertain_reminder_without_history': await uncertain_reminder_probe(history_available=False),
            'uncertain_report_delivery': await uncertain_report_probe(),
            'retention_observability': {
                'real_user_retention_computed': False,
                'available_post_v3_signal': 'Persisted solve received_at/date, member enrollment periods, daily buckets, timezone/deadline snapshots.',
                'missing_signal': 'No command-view/session/visit events, reminder impressions/clicks, exposure experiments, or precise original acquisition timestamp.',
                'legacy_limit': 'v2 has lifetime totals plus at most 30 daily buckets; previous joins may have been overwritten; missing days are unknown.',
                'cohort_caveats': 'Use first observed post-v3 activation cohort; exclude unknown coverage and right-censored users; leavers remain in original cohort denominator.',
                'interpretation': 'A D7/D28 solve-active metric is not user-return retention and cannot be claimed from synthetic measurements.',
            },
        }
        return results
    finally:
        logging.disable(previous_disable)


async def main():
    print(json.dumps(await run_probes(), indent=2))


if __name__ == '__main__':
    asyncio.run(main())
