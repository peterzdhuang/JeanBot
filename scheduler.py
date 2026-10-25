"""Batched, durable reminder/report delivery without loading lifetime history.

Discord sends and PostgreSQL commits are separate. Persist intent before sending,
then recover uncertain sends by marker. If history cannot be read, defer rather
than blindly sending a duplicate. All user history remains in storage.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from tracker import challenge_day, daily_quest, deadline, due_reports, reminder_due, sync_roster, utcnow

log = logging.getLogger("arena")
UNKNOWN = object()
COMPLETE = {"sent", "cancelled"}
MAX_BATCH = 50


def _edit(store, guild_id, now):
    if hasattr(store, "scheduler_edit"):
        return store.scheduler_edit(guild_id, now)
    return store.edit(guild_id)  # In-memory transport test harness.


def _day(member, now):
    from tracker import member_day
    return member_day(member, now)


def _mentions(factory, keys=None):
    return factory(keys) if factory else None


def _stamp(now):
    return now.astimezone(timezone.utc).isoformat()


def _reminder_text(event, quest):
    end = datetime.fromisoformat(event["deadline"])
    text = (" ".join(f"<@{key}>" for key in event["members"])
            + " Time for your LeetCode practice.\n"
            + f"Deadline <t:{int(end.timestamp())}:R>. Submit your attempt link to record your solve.\n"
            + "Snooze: `/snooze` · Settings: `/reminders` · Opt out: `/reminders enabled:false`.")
    if quest:
        label = "Easy + Medium-level constraint" if quest.get("constraint") else quest.get("difficulty", "Medium")
        text += f"\nToday's quest: [{quest['name']}]({quest['url']}) · {label}"
        if quest.get("constraint"):
            text += f"\n**Required constraint:** {quest['constraint']}\nSubmitting attests that you followed the constraint."
    return text + f"\n`{event['marker']}`"


def _prepare_reminders(data, guild_id, now):
    outbox = data.setdefault("reminders", {})
    covered = {(event["day"], event["slot"], user_id)
               for event in outbox.values() for user_id in event.get("members", [])}
    groups = {}
    for user_id, member in data["members"].items():
        slot = reminder_due(data, member, now)
        if not slot:
            continue
        day = _day(member, now)
        if (day.isoformat(), slot, user_id) in covered:
            continue
        row = data.get("roster", {}).get(day.isoformat(), {}).get(user_id)
        end = row["deadline"] if row else deadline(day, member["timezone"]).isoformat()
        groups.setdefault((day.isoformat(), slot, member["timezone"], end), []).append(user_id)
    for (day, slot, zone, end), users in groups.items():
        try:
            quest = daily_quest(guild_id, datetime.fromisoformat(day).date(), data)
        except ValueError:
            quest = None
        remaining = sorted(users, key=int)
        while remaining:
            batch = remaining[:MAX_BATCH]
            while batch:
                digest = hashlib.sha256(f"{guild_id}:{day}:{slot}:{zone}:{','.join(batch)}".encode()).hexdigest()[:24]
                key = f"{day}:{slot}:{digest}"
                event = {"status": "pending", "day": day, "slot": slot, "timezone": zone,
                         "deadline": end, "members": list(batch), "created_at": _stamp(now),
                         "marker": f"lc-reminder:{guild_id}:{key}"}
                content = _reminder_text(event, quest)
                if len(content) <= 1950:
                    event["content"] = content
                    outbox[key] = event
                    remaining = remaining[len(batch):]
                    break
                batch = batch[:-1]
            if not batch:
                raise ValueError("Quest constraint is too long for a reminder message.")


async def _recover(bot, channel, marker, attempted_at):
    """None means checked and absent; UNKNOWN means sending again is unsafe."""
    if hasattr(channel, "history"):
        try:
            after = datetime.fromisoformat(attempted_at) - timedelta(minutes=2)
            author_id = getattr(getattr(bot, "user", None), "id", None)
            if author_id is None:
                return UNKNOWN
            # No fixed 100-message cap: large reminder bursts must remain recoverable.
            async for message in channel.history(limit=None, after=after):
                if message.author.id == author_id and f"`{marker}`" in message.content:
                    return message.id
            return None
        except Exception as exc:
            log.warning("Delivery reconciliation unavailable (%s); deferring uncertain send.", type(exc).__name__)
            return UNKNOWN
    # Test transports can inject recovery directly. Real TextChannels use history.
    finder = getattr(bot, "find_report_message", None)
    if finder is not None:
        try:
            return await finder(channel, marker)
        except Exception:
            return UNKNOWN
    return UNKNOWN


def _mark_sent(data, event, message_id, now):
    event.update(status="sent", message_id=message_id, sent_at=_stamp(now))
    for user_id in event["members"]:
        member = data["members"].get(user_id)
        if member is None:
            continue
        marks = member.setdefault("reminder_marks", {})
        marks[f"{event['day']}:{event['slot']}"] = event["day"]
        marks[event["slot"]] = max(marks.get(event["slot"], ""), event["day"])


async def _deliver_reminder(bot, guild_id, event_id, channel, clock, mention_factory, reminder_view):
    now = clock()
    async with _edit(bot.store, guild_id, now) as data:
        event = dict(data.get("reminders", {}).get(event_id, {}))
    if not event or event.get("status") in COMPLETE:
        return
    attempted = event.get("attempted_at")
    if attempted:
        recovered = await _recover(bot, channel, event["marker"], attempted)
        if recovered is UNKNOWN:
            async with _edit(bot.store, guild_id, clock()) as data:
                data["reminders"][event_id]["status"] = "uncertain"
            return
        if recovered is not None:
            async with _edit(bot.store, guild_id, clock()) as data:
                _mark_sent(data, data["reminders"][event_id], recovered, clock())
            return
    # Recheck opt-out, snooze, completion and deadline immediately before intent.
    now = clock()
    async with _edit(bot.store, guild_id, now) as data:
        current = data["reminders"][event_id]
        if current.get("status") in COMPLETE:
            return
        batch = [key for key in current["members"] if key in data["members"]
                 and reminder_due(data, data["members"][key], now) == current["slot"]
                 and _day(data["members"][key], now).isoformat() == current["day"]]
        if not batch:
            current.update(status="cancelled", cancelled_at=_stamp(now))
            return
        current["members"] = batch
        current["content"] = _reminder_text(current, data.get("quests", {}).get(current["day"]))
        current.update(status="attempting", attempted_at=_stamp(now))
        event = dict(current)
    kwargs = {"allowed_mentions": _mentions(mention_factory, event["members"])}
    if reminder_view:
        kwargs["view"] = reminder_view()
    sent = await channel.send(event["content"], **kwargs)
    async with _edit(bot.store, guild_id, clock()) as data:
        _mark_sent(data, data["reminders"][event_id], sent.id, clock())


async def _deliver_report(bot, guild_id, day, channel, clock, mention_factory, report_builder):
    key = day.isoformat()
    while True:
        now = clock()
        async with _edit(bot.store, guild_id, now) as data:
            due = dict(due_reports(data, now))
            if day not in due:
                return
            delivery = data["reports"].setdefault(key, {})
            if delivery.get("complete"):
                return
            delivery.setdefault("complete", False)
            delivery.setdefault("created_at", _stamp(now))
            delivery.setdefault("message_ids", [])
            index = len(delivery["message_ids"])
            # Refresh the roster until the first send intent freezes publication.
            if "pages" not in delivery or (index == 0 and not delivery.get("published_at")):
                delivery["pages"] = report_builder(guild_id, day, due[day], data.get("quests", {}).get(key))
            if index >= len(delivery["pages"]):
                delivery.update(complete=True, delivered_at=_stamp(now))
                return
            previous_attempt = delivery.get("attempting_page") == index
            attempted_at = delivery.get("attempted_at", _stamp(now))
            marker = f"lc-report:{guild_id}:{day}:{index}"
            content = delivery["pages"][index]
        # Check any prior uncertain delivery, as well as legacy orphan sends.
        recovered = await _recover(bot, channel, marker, attempted_at)
        if recovered is UNKNOWN and previous_attempt:
            return
        if recovered is UNKNOWN:
            recovered = None  # A new page has no previous send to duplicate.
        if recovered is None:
            async with _edit(bot.store, guild_id, clock()) as data:
                due = dict(due_reports(data, clock()))
                if day not in due:
                    return
                delivery = data["reports"][key]
                if index != len(delivery["message_ids"]):
                    continue
                if index == 0 and not delivery.get("published_at"):
                    delivery["pages"] = report_builder(guild_id, day, due[day], data.get("quests", {}).get(key))
                    content = delivery["pages"][index]
                delivery.setdefault("published_at", _stamp(clock()))
                delivery.update(attempting_page=index, attempted_at=_stamp(clock()))
            sent = await channel.send(content, allowed_mentions=_mentions(mention_factory))
            recovered = sent.id
        async with _edit(bot.store, guild_id, clock()) as data:
            delivery = data["reports"][key]
            if len(delivery["message_ids"]) == index:
                delivery["message_ids"].append(recovered)
            delivery.setdefault("published_at", _stamp(clock()))
            delivery.pop("attempting_page", None)


async def process_guild(bot, guild_id, *, clock=utcnow, mention_factory=None,
                        report_builder=None, reminder_view=None):
    """One bounded scan, transactions per delivery batch, zero per-member scans."""
    now = clock()
    async with _edit(bot.store, guild_id, now) as data:
        sync_roster(data, now, guild_id=guild_id)
        channel_id = data.get("report_channel_id")
        if not channel_id:
            return
        _prepare_reminders(data, guild_id, now)
        pending = [key for key, event in data.get("reminders", {}).items()
                   if event.get("status") not in COMPLETE]
        pending.sort(key=lambda key: (data["reminders"][key].get("status") == "uncertain",
                                      data["reminders"][key].get("created_at", "")))
        reports = [day for day, roster in due_reports(data, now)][:5]
    if not pending and not reports:
        return
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    for event_id in pending[:20]:
        await _deliver_reminder(bot, guild_id, event_id, channel, clock, mention_factory, reminder_view)
    if report_builder:
        for day in reports:
            await _deliver_report(bot, guild_id, day, channel, clock, mention_factory, report_builder)
