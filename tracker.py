"""Compact counters, personal challenge clocks, and legacy JSON conversion."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone as UTC
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from catalog import DIFFICULTY, POOLS, resolve_difficulty, title

LEVEL_XP = 250
DAILY_XP = 25
REWARDS = {"Easy": (20, 50), "Medium": (50, 100), "Hard": (100, 180)}
QUEST_REWARDS = {"Easy": (10, 50), "Medium": (25, 100), "Hard": (50, 200)}
DIFFICULTIES = tuple(POOLS)
REWARD_LIMIT = 10
RECENT_DAYS = 30
CUTOFF = time(3, 15)
REMINDER_TIMES = ("20:00", "02:15")
STREAK_MILESTONES = (3, 7, 14, 30, 60, 100, 365)
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TZ_ALIASES = {
    **dict.fromkeys(("PST", "PDT", "PT"), "America/Los_Angeles"),
    **dict.fromkeys(("MST", "MDT", "MT"), "America/Denver"),
    **dict.fromkeys(("CST", "CDT", "CT"), "America/Chicago"),
    **dict.fromkeys(("EST", "EDT", "ET"), "America/New_York"),
    "UTC": "UTC", "GMT": "UTC",
}


def utcnow() -> datetime:
    return datetime.now(UTC.utc)


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(TZ_ALIASES.get(name.strip().upper(), name.strip()))
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError("Use PST, MST, CST, EST, UTC, or a timezone like America/Phoenix.") from exc


def challenge_day(now: datetime, zone: str) -> date:
    if now.tzinfo is None:
        raise ValueError("The challenge clock needs a timezone-aware datetime.")
    local = now.astimezone(timezone(zone))
    return local.date() - timedelta(days=local.time() < CUTOFF)


def deadline(day: date, zone: str) -> datetime:
    return datetime.combine(day + timedelta(days=1), CUTOFF, timezone(zone)).astimezone(UTC.utc)


def daily_quest(guild_id: int, day: date, difficulty: str = "Easy") -> dict:
    pool = POOLS[difficulty]
    slug = pool[(day.toordinal() + guild_id) % len(pool)]
    coins, xp = QUEST_REWARDS[difficulty]
    return {"slug": slug, "name": title(slug), "difficulty": difficulty,
            "url": f"https://leetcode.com/problems/{slug}/", "coins": coins, "xp": xp}


def problem_from_url(raw: str) -> tuple[str, str]:
    parsed = urlparse(raw.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("That LeetCode link has an invalid port.") from exc
    if parsed.scheme != "https" or parsed.hostname not in {"leetcode.com", "www.leetcode.com"}:
        raise ValueError("Paste an HTTPS leetcode.com problem or problem-specific submission link.")
    if parsed.username or parsed.password or port or parsed.query or parsed.fragment:
        raise ValueError("Use a plain LeetCode link without query parameters or a fragment.")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "problems" or not SLUG.fullmatch(parts[1]):
        raise ValueError("Use a link like https://leetcode.com/problems/two-sum/submissions/12345/.")
    rest = parts[2:]
    if rest not in ([], ["description"], ["submissions"]) and not (
        len(rest) == 2 and rest[0] == "submissions" and rest[1].isdigit()
    ):
        raise ValueError("Use a problem link or a submission link for that problem.")
    return parts[1], f"https://leetcode.com/problems/{parts[1]}/"


def new_guild() -> dict:
    return {"timezone": "America/Vancouver", "report_channel_id": None,
            "reminders_enabled": True, "reminder_times": list(REMINDER_TIMES),
            "members": {}, "legacy_imported": False}


def new_member(day: date, zone: str) -> dict:
    return {"joined": day.isoformat(), "active": True, "timezone": timezone(zone).key,
            "reminders": True, "left_day": None, "solved": 0, "counts": [0, 0, 0],
            "period_solved": 0, "completed_days": 0, "last_solved": None,
            "streak": 0, "longest": 0, "xp": 0, "quests": 0,
            "recent": {}, "wallet": 0, "inventory": {}, "equipped": {},
            "pet": None, "run": None, "last_run_day": None, "dungeon_wins": 0,
            "best_floor": 0, "reminder_marks": {},
            "reported_day": (day - timedelta(days=1)).isoformat()}


def enroll(guild: dict, user_id: int, day: date, zone: str | None = None) -> bool:
    key = str(user_id)
    current = guild["members"].get(key)
    zone = timezone(zone or (current or {}).get("timezone", guild["timezone"])).key
    if current and current["active"]:
        if zone != current["timezone"] and deadline(day, zone) != deadline(day, current["timezone"]):
            raise ValueError("Leave and rejoin to change timezone; this resets your current streak, not your wallet.")
        current["timezone"] = zone
        return False
    member = new_member(day, zone)
    if current:
        for field in ("reminders", "solved", "counts", "longest", "xp", "quests", "wallet",
                      "inventory", "equipped", "pet", "run", "last_run_day", "dungeon_wins", "best_floor",
                      "recent", "reminder_marks"):
            member[field] = current[field]
        # Keep today's reward cap and quest claims across a leave/rejoin cycle.
        # A day already completed is still completed in the new tracking period.
        today_count = sum(member["recent"].get(day.isoformat(), {}).get("counts", []))
        if today_count:
            member.update(completed_days=1, period_solved=today_count,
                          last_solved=day.isoformat(), streak=1)
        prune_recent(member, day)
    guild["members"][key] = member
    return True


def prune_recent(member: dict, today: date) -> None:
    minimum = (today - timedelta(days=RECENT_DAYS - 1)).isoformat()
    member["recent"] = {day: state for day, state in member["recent"].items()
                        if minimum <= day <= today.isoformat()}


def record(guild: dict, user_id: int, day: date, slug: str, *, guild_id: int,
           difficulty: str | None = None, now: datetime | None = None) -> dict:
    """Count a trusted submission. Never retain its slug, URL, or submission ID."""
    from game import advance_run, pet_solve_bonus

    member = guild["members"][str(user_id)]
    if not member["active"] or day < date.fromisoformat(member["joined"]):
        raise ValueError("Join the challenge before submitting.")
    if member["last_solved"] and day < date.fromisoformat(member["last_solved"]):
        raise ValueError("Only the current challenge day can accept submissions.")
    difficulty = resolve_difficulty(slug, difficulty)
    index = DIFFICULTIES.index(difficulty)
    prune_recent(member, day)
    state = member["recent"].setdefault(day.isoformat(), {"counts": [0, 0, 0], "quests": 0})
    first = sum(state["counts"]) == 0
    eligible = sum(state["counts"]) < REWARD_LIMIT
    state["counts"][index] += 1
    member["counts"][index] += 1
    member["solved"] += 1
    member["period_solved"] += 1
    if first:
        previous = date.fromisoformat(member["last_solved"]) if member["last_solved"] else None
        member["streak"] = member["streak"] + 1 if previous == day - timedelta(days=1) else 1
        member["longest"] = max(member["longest"], member["streak"])
        member["last_solved"] = day.isoformat()
        member["completed_days"] += 1
    coins, xp = REWARDS[difficulty] if eligible else (0, 0)
    if first:
        coins += 10
        xp += DAILY_XP
    quest = slug == daily_quest(guild_id, day, difficulty)["slug"] and not state["quests"] & (1 << index)
    if quest:
        state["quests"] |= 1 << index
        bonus_coins, bonus_xp = QUEST_REWARDS[difficulty]
        coins += bonus_coins
        xp += bonus_xp
        member["quests"] += 1
    event = None
    if eligible:
        if member["equipped"].get("relic") == "magnet":
            coins += REWARDS[difficulty][0] // 10
        coins += pet_solve_bonus(member, difficulty, now or utcnow())
        event = advance_run(member, difficulty)
    member["wallet"] += coins
    member["xp"] += xp
    return {"coins": coins, "xp": xp, "quest": quest, "first": first,
            "difficulty": difficulty, "rewarded": eligible, "dungeon": event}


def level_title(level: int) -> str:
    return next((name for minimum, name in ((20, "Legend"), (10, "Grandmaster"),
                                           (5, "Algorithm Knight"), (3, "Code Ranger"))
                 if level >= minimum), "Rookie")


def stats(guild: dict, user_id: int, today: date) -> dict | None:
    member = guild["members"].get(str(user_id))
    if member is None:
        return None
    joined = date.fromisoformat(member["joined"])
    if not member["active"] and member["left_day"]:
        today = min(today, date.fromisoformat(member["left_day"]))
    count = sum(member["recent"].get(today.isoformat(), {}).get("counts", []))
    # A dormant profile can keep only 30 count buckets while last_solved is lifelong.
    completed_today = member["last_solved"] == today.isoformat()
    last = date.fromisoformat(member["last_solved"]) if member["last_solved"] else None
    streak = member["streak"] if last in (today, today - timedelta(days=1)) else 0
    completed_closed = member["completed_days"] - int(last == today)
    missed = max(0, (today - joined).days - completed_closed)
    level = member["xp"] // LEVEL_XP + 1
    result = {"streak": streak, "missed": missed, "solved": member["solved"],
              "period_solved": member["period_solved"], "today": completed_today,
              "today_count": count, "active": member["active"], "joined": joined,
              "longest": member["longest"], "completed_days": member["completed_days"],
              "days": {date.fromisoformat(key) for key, state in member["recent"].items()
                       if key <= today.isoformat() and sum(state["counts"])},
              "xp": member["xp"], "level": level, "level_xp": member["xp"] % LEVEL_XP,
              "title": level_title(level), "quests": member["quests"], "wallet": member["wallet"],
              "counts": member["counts"]}
    result["badges"] = [label for label, field, target in (
        ("🌱 First Blood", "solved", 1), ("🔥 Heating Up", "longest", 3),
        ("⚡ Week Warrior", "longest", 7), ("💎 Locked In", "longest", 14),
        ("👑 Unstoppable", "longest", 30), ("🚀 Problem Hunter", "solved", 10),
        ("🧠 Algorithm Alchemist", "solved", 50), ("🏆 Centurion", "solved", 100),
        ("🎲 Sidequest Specialist", "quests", 7)) if result[field] >= target]
    return result


def rankings(guild: dict, now: datetime) -> list[tuple[str, dict]]:
    rows = [(key, stats(guild, int(key), challenge_day(now, member["timezone"])))
            for key, member in guild["members"].items() if member["active"]]
    return sorted(rows, key=lambda row: (-row[1]["streak"], row[1]["missed"], -row[1]["xp"], int(row[0])))


def daily_report(guild: dict, day: date, zone: str | None = None) -> tuple[list[str], list[str]]:
    done, pending = [], []
    for key, member in guild["members"].items():
        if not member["active"] or member["joined"] > day.isoformat() or (zone and member["timezone"] != zone):
            continue
        (done if sum(member["recent"].get(day.isoformat(), {}).get("counts", [])) else pending).append(key)
    return done, pending


def reminder_due(guild: dict, member: dict, now: datetime) -> str | None:
    if not guild["reminders_enabled"] or not member["active"] or not member["reminders"]:
        return None
    day = challenge_day(now, member["timezone"])
    if sum(member["recent"].get(day.isoformat(), {}).get("counts", [])):
        return None
    local = now.astimezone(timezone(member["timezone"]))
    minute = local.hour * 60 + local.minute
    for slot in guild["reminder_times"]:
        hour, minutes = map(int, slot.split(":"))
        if 0 <= minute - (hour * 60 + minutes) < 60 and member["reminder_marks"].get(slot) != day.isoformat():
            return slot
    return None


def migrate_legacy(source: dict, now: datetime) -> dict[str, dict]:
    """Import only aggregates; all problem identifiers stay out of the output."""
    if source.get("version") != 1 or not isinstance(source.get("guilds"), dict):
        raise ValueError("Unsupported legacy JSON. Expected a version 1 tracker file.")
    converted = {}
    for guild_id, old in source["guilds"].items():
        int(guild_id)
        guild = new_guild()
        guild["timezone"] = timezone(old.get("timezone", guild["timezone"])).key
        for field in ("report_channel_id", "reminders_enabled"):
            if field in old:
                guild[field] = old[field]
        for user_id, old_member in old.get("members", {}).items():
            int(user_id)
            today = challenge_day(now, guild["timezone"])
            calendar_day = now.astimezone(timezone(guild["timezone"])).date()
            original_joined = date.fromisoformat(old_member["joined"])
            joined = min(original_joined, today) if original_joined <= calendar_day else original_joined
            member = new_member(joined, guild["timezone"])
            member["active"] = old_member.get("active", True)
            member["reminders"] = old_member.get("reminders", True)
            member["left_day"] = today.isoformat() if not member["active"] else None
            all_entries = [entry for entry in old.get("submissions", [])
                           if str(entry["user_id"]) == user_id and date.fromisoformat(entry["date"]) <= calendar_day]
            entries = [{**entry, "date": min(date.fromisoformat(entry["date"]), today).isoformat()}
                       for entry in all_entries if date.fromisoformat(entry["date"]) >= original_joined]
            days = sorted({entry["date"] for entry in entries})
            member["solved"] = len(all_entries)
            member["period_solved"] = len(entries)
            member["completed_days"] = len(days)
            previous = None
            for value in days:
                day = date.fromisoformat(value)
                member["streak"] = member["streak"] + 1 if previous == day - timedelta(days=1) else 1
                member["longest"] = max(member["longest"], member["streak"])
                previous = day
            member["last_solved"] = days[-1] if days else None
            quest_days = {entry["date"] for entry in entries if entry.get("quest_xp", 0) > 0}
            member["quests"] = len(quest_days)
            member["xp"] = len({entry["slug"] for entry in entries}) * 50 + len(days) * 25 + len(quest_days) * 50
            for entry in all_entries:
                index = DIFFICULTIES.index(DIFFICULTY.get(entry["slug"], "Easy"))
                member["counts"][index] += 1
            for entry in entries:
                index = DIFFICULTIES.index(DIFFICULTY.get(entry["slug"], "Easy"))
                state = member["recent"].setdefault(entry["date"], {"counts": [0, 0, 0], "quests": 0})
                state["counts"][index] += 1
                if entry.get("quest_xp", 0):
                    state["quests"] |= 1 << index
            prune_recent(member, today)
            member["reported_day"] = (today - timedelta(days=1)).isoformat()
            guild["members"][user_id] = member
        guild["legacy_imported"] = True
        converted[guild_id] = guild
    return converted
