"""LeetCode tracking, durable history, and personal challenge clocks.

Legacy game fields remain inert so an upgrade never destroys old snapshots.
"""
from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta, timezone as UTC
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from catalog import DIFFICULTY, title

DIFFICULTIES = ("Easy", "Medium", "Hard")
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


def daily_quest(guild_id: int, day: date, guild: dict | None = None) -> dict:
    key = day.isoformat()
    if guild is not None and key in guild.setdefault("quests", {}):
        return guild["quests"][key]
    from quest_catalog import select_quest
    quest = select_quest(guild_id, day)
    if guild is not None:
        guild["quests"][key] = quest
    return quest


def _url(raw: str):
    parsed = urlparse(raw.strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("That LeetCode link has an invalid port.") from exc
    if (parsed.scheme != "https" or parsed.hostname not in {"leetcode.com", "www.leetcode.com"}
            or parsed.username or parsed.password or port):
        raise ValueError("Paste an HTTPS leetcode.com submission link.")
    return parsed


def problem_from_url(raw: str) -> tuple[str, str]:
    parts = _url(raw).path.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "problems" or not SLUG.fullmatch(parts[1]):
        raise ValueError("Supply a problem URL such as https://leetcode.com/problems/two-sum/.")
    if parts[2:] not in ([], ["description"], ["submissions"]) and not (
            len(parts) == 4 and parts[2] == "submissions" and parts[3].isascii() and parts[3].isdigit()):
        raise ValueError("Unsupported LeetCode problem link.")
    return parts[1], f"https://leetcode.com/problems/{parts[1]}/"


def submission_from_url(raw: str, problem_url: str | None = None) -> dict:
    parts = _url(raw).path.strip("/").split("/")
    if (len(parts) == 4 and parts[0] == "problems" and SLUG.fullmatch(parts[1])
            and parts[2] == "submissions" and parts[3].isascii() and parts[3].isdigit()):
        slug, submission_id = parts[1], str(int(parts[3]))
        normalized = f"https://leetcode.com/problems/{slug}/submissions/{submission_id}/"
        if problem_url and problem_from_url(problem_url)[0] != slug:
            raise ValueError("The problem URL must match the problem in the submission URL.")
    elif (len(parts) == 3 and parts[:2] == ["submissions", "detail"]
            and parts[2].isascii() and parts[2].isdigit()):
        if not problem_url:
            raise ValueError("For a /submissions/detail/ link, also supply the problem_url field.")
        slug = problem_from_url(problem_url)[0]
        submission_id = str(int(parts[2]))
        normalized = f"https://leetcode.com/submissions/detail/{submission_id}/"
    else:
        raise ValueError("Paste the actual submission link ending /submissions/<id>/, not just a problem link.")
    return {"slug": slug, "url": normalized, "submission_id": submission_id,
            "problem_url": f"https://leetcode.com/problems/{slug}/"}


def new_guild() -> dict:
    return {"timezone": "America/Vancouver", "report_channel_id": None,
            "reminders_enabled": True, "reminder_times": list(REMINDER_TIMES),
            "members": {}, "legacy_imported": False, "quests": {}, "reports": {}, "roster": {}, "buddies": {}}


def new_member(day: date, zone: str) -> dict:
    return {"joined": day.isoformat(), "active": True, "timezone": timezone(zone).key,
            "reminders": True, "left_day": None, "solved": 0, "counts": [0, 0, 0],
            "period_solved": 0, "completed_days": 0, "last_solved": None,
            "streak": 0, "longest": 0, "xp": 0, "quests": 0,
            "recent": {}, "wallet": 0, "inventory": {}, "equipped": {},
            "pet": None, "run": None, "last_run_day": None, "dungeon_wins": 0,
            "best_floor": 0, "reminder_marks": {},
            "reported_day": (day - timedelta(days=1)).isoformat(),
            "submissions": [], "unknown_count": 0, "coverage_start": day.isoformat(),
            "periods": [{"start": day.isoformat(), "end": None, "timezone": timezone(zone).key}],
            "quest_start": day.isoformat(), "roster_start": day.isoformat(),
            "weekly_goal": 4, "reminder_time": "20:00", "late_reminder": False,
            "snoozed_until": None, "pending_timezone": None, "excused_days": []}


def upgrade_member(member: dict, today: date | None = None) -> dict:
    """Add fields without replacing or dropping any legacy value."""
    today = today or challenge_day(utcnow(), member.get("timezone", "UTC"))
    # None means inherited server schedule for old members; new users default to evening only.
    member.setdefault("reminder_time", None)
    member.setdefault("submissions", [])
    member.setdefault("unknown_count", 0)
    member.setdefault("coverage_start", today.isoformat())
    member.setdefault("quest_start", today.isoformat())
    member.setdefault("roster_start", today.isoformat())
    member.setdefault("periods", [{"start": member["joined"], "end": member.get("left_day")
                                  if not member.get("active", True) else None}])
    for period in member["periods"]:
        period.setdefault("timezone", member.get("timezone", "UTC"))
    defaults = new_member(date.fromisoformat(member["joined"]), member.get("timezone", "UTC"))
    for key, value in defaults.items():
        member.setdefault(key, copy.deepcopy(value))
    return member


def member_day(member: dict, now: datetime) -> date:
    """Resolve a future timezone transition without ever reversing a challenge label."""
    zone = member["timezone"]
    floor = member.get("timezone_day_floor")
    pending = member.get("pending_timezone")
    if pending and now >= datetime.fromisoformat(pending["effective_at"]):
        zone = pending["timezone"]
        floor = (date.fromisoformat(pending["after_day"]) + timedelta(days=1)).isoformat()
    day = challenge_day(now, zone)
    return max(day, date.fromisoformat(floor)) if floor else day


def change_timezone(guild: dict, user_id: int, zone: str, now: datetime) -> dict:
    member = guild["members"][str(user_id)]
    upgrade_member(member, member_day(member, now))
    apply_pending_timezone(guild, user_id, now)
    resolved = timezone(zone).key
    if resolved == member["timezone"]:
        member["pending_timezone"] = None
        return {"timezone": resolved, "effective_at": now.astimezone(UTC.utc).isoformat(),
                "after_day": member_day(member, now).isoformat()}
    sync_roster(guild, now)
    day = member_day(member, now)
    row = guild["roster"].get(day.isoformat(), {}).get(str(user_id))
    cutoff = row["deadline"] if row else deadline(day, member["timezone"]).isoformat()
    if challenge_day(datetime.fromisoformat(cutoff), resolved) > day + timedelta(days=1):
        raise ValueError("This timezone jump would skip a challenge date. Change through an intermediate timezone after your next deadline to preserve your streak and day history.")
    pending = {"timezone": resolved, "effective_at": cutoff, "after_day": day.isoformat()}
    member["pending_timezone"] = pending
    return dict(pending)


def apply_pending_timezone(guild: dict, user_id: int, now: datetime) -> bool:
    member = guild["members"][str(user_id)]
    pending = member.get("pending_timezone")
    if not pending or now < datetime.fromisoformat(pending["effective_at"]):
        return False
    after = date.fromisoformat(pending["after_day"])
    if member.get("active", True):  # A member who left keeps the zone but gains no new period.
        for period in member.get("periods", []):
            if period.get("end") is None:
                period["end"] = after.isoformat()
        member["periods"].append({"start": (after + timedelta(days=1)).isoformat(), "end": None,
                                  "timezone": pending["timezone"]})
    member.setdefault("timezone_history", []).append({"from": member["timezone"],
        "to": pending["timezone"], "effective_at": pending["effective_at"], "after_day": pending["after_day"]})
    member["timezone"] = pending["timezone"]
    member["timezone_day_floor"] = (after + timedelta(days=1)).isoformat()
    member["pending_timezone"] = None
    return True


def reminder_slots(guild: dict, member: dict) -> list[str]:
    if member.get("reminder_time") is None:
        return list(guild["reminder_times"])
    slots = [member["reminder_time"]]
    if member.get("late_reminder"):
        slots.append("02:15")
    return list(dict.fromkeys(slots))


def set_reminder_preferences(member: dict, reminder_time: str | None = None,
                             late_reminder: bool | None = None, enabled: bool | None = None) -> None:
    if reminder_time is not None:
        if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", reminder_time):
            raise ValueError("Use a local reminder time in HH:MM format, such as 20:00.")
        if reminder_time.startswith("03:"):
            raise ValueError("Avoid the 03:00 hour because it straddles the 03:15 cutoff.")
    if reminder_time is not None or late_reminder is not None:
        member["reminder_time"] = reminder_time or member.get("reminder_time") or "20:00"
    if late_reminder is not None:
        member["late_reminder"] = bool(late_reminder)
    if enabled is not None:
        member["reminders"] = bool(enabled)


def snooze_member(member: dict, now: datetime, minutes: int = 60) -> str:
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
        raise ValueError("Choose a snooze duration from 1 to 1440 minutes.")
    member["snoozed_until"] = (now.astimezone(UTC.utc) + timedelta(minutes=minutes)).isoformat()
    member["snoozed_day"] = member_day(member, now).isoformat()
    return member["snoozed_until"]


def _buddy_key(first: int | str, second: int | str) -> str:
    return ":".join(sorted((str(first), str(second)), key=int))


def invite_buddy(guild: dict, inviter: int, invitee: int) -> dict:
    if str(inviter) == str(invitee):
        raise ValueError("Choose another member as your buddy.")
    for user_id in (inviter, invitee):
        if str(user_id) not in guild["members"] or not guild["members"][str(user_id)]["active"]:
            raise ValueError("Both buddies must be active tracker members.")
    buddies = guild.setdefault("buddies", {})
    key = _buddy_key(inviter, invitee)
    existing = buddies.get(key)
    if existing and existing["status"] in {"pending", "accepted"}:
        raise ValueError("This buddy connection already exists; the invited member can accept it.")
    pair = {"inviter": str(inviter), "invitee": str(invitee), "status": "pending"}
    buddies[key] = pair
    return dict(pair)


def accept_buddy(guild: dict, invitee: int, inviter: int) -> dict:
    pair = guild.get("buddies", {}).get(_buddy_key(inviter, invitee))
    if not pair or pair["status"] != "pending" or pair["invitee"] != str(invitee) or pair["inviter"] != str(inviter):
        raise ValueError("There is no pending buddy invitation from that member for you.")
    if not all(guild["members"].get(str(value), {}).get("active") for value in (inviter, invitee)):
        raise ValueError("Both buddies must be active tracker members.")
    pair["status"] = "accepted"
    return dict(pair)


def remove_buddy(guild: dict, user_id: int, buddy_id: int) -> None:
    pair = guild.get("buddies", {}).get(_buddy_key(user_id, buddy_id))
    if not pair or str(user_id) not in (pair["inviter"], pair["invitee"]):
        raise ValueError("No buddy connection exists with that member.")
    pair["status"] = "removed"


def buddy_progress(guild: dict, user_id: int, now: datetime) -> list[dict]:
    result = []
    for pair in guild.get("buddies", {}).values():
        if pair["status"] != "accepted" or str(user_id) not in (pair["inviter"], pair["invitee"]):
            continue
        other = pair["invitee"] if pair["inviter"] == str(user_id) else pair["inviter"]
        member = guild["members"].get(other)
        if member is None:
            continue
        day = member_day(member, now)
        state = stats(guild, int(other), day)
        result.append({"user_id": other, "streak": state["streak"], "today_count": state["today_count"],
                       "week": progress(member, day, "week", guild)})
    return result


def enroll(guild: dict, user_id: int, day: date, zone: str | None = None) -> bool:
    key = str(user_id)
    current = guild["members"].get(key)
    zone = timezone(zone or (current or {}).get("timezone", guild["timezone"])).key
    if current:
        upgrade_member(current, day)
        if current["active"]:
            if zone != current["timezone"] and deadline(day, zone) != deadline(day, current["timezone"]):
                raise ValueError("Leave and rejoin to change timezone; history and lifetime totals are preserved.")
            if zone != current["timezone"]:
                for period in current["periods"]:
                    if period["end"] is None:
                        period["end"] = (day - timedelta(days=1)).isoformat()
                current["periods"].append({"start": day.isoformat(), "end": None, "timezone": zone})
            current["timezone"] = zone
            return False
        # Keep the whole old object, including unknown fields and all history.
        _archive_period(current)
        count = day_count(current, day)
        for period in current["periods"]:
            if period["end"] is None:
                period["end"] = current.get("left_day") or day.isoformat()
        current["periods"].append({"start": day.isoformat(), "end": None, "timezone": zone})
        row = guild.get("roster", {}).get(day.isoformat(), {}).get(key)
        if row is not None and not row.get("closed"):
            row["excused"] = False
            current["excused_days"] = [value for value in current.get("excused_days", []) if value != day.isoformat()]
        current.update(joined=day.isoformat(), active=True, timezone=zone, left_day=None, pending_timezone=None,
                       period_solved=count, completed_days=int(bool(count)),
                       streak=int(bool(count)), last_solved=day.isoformat() if count else None)
    else:
        guild["members"][key] = new_member(day, zone)
    return True


def _archive_period(member: dict) -> None:
    summary = {key: copy.deepcopy(member.get(key)) for key in ("joined", "left_day", "timezone",
               "period_solved", "completed_days", "last_solved", "streak", "longest")}
    archive = member.setdefault("tracking_period_history", [])
    if not archive or archive[-1] != summary:
        archive.append(summary)


def leave_member(guild: dict, user_id: int, now: datetime) -> None:
    sync_roster(guild, now)
    member = guild["members"][str(user_id)]
    day = member_day(member, now)
    member.update(active=False, left_day=day.isoformat())
    _archive_period(member)
    for period in member["periods"]:
        if period["end"] is None:
            period["end"] = day.isoformat()
    for roster in guild["roster"].values():
        row = roster.get(str(user_id))
        if row and not row["closed"] and now < datetime.fromisoformat(row["deadline"]):
            row["excused"] = True
            if day.isoformat() not in member["excused_days"]:
                member["excused_days"].append(day.isoformat())


def prune_recent(member: dict, today: date) -> None:
    """Compatibility hook: history is intentionally never pruned."""


def day_count(member: dict, day: date | str) -> int:
    key = day if isinstance(day, str) else day.isoformat()
    state = member.get("recent", {}).get(key, {})
    return sum(state.get("counts", [])) + state.get("unknown", 0)


def record(guild: dict, user_id: int, day: date, slug: str, *, guild_id: int,
           difficulty: str | None = None, now: datetime | None = None,
           submission_url: str | None = None, submission_id: str | None = None) -> dict:
    member = guild["members"][str(user_id)]
    upgrade_member(member, day)
    if not member["active"] or day < date.fromisoformat(member["joined"]):
        raise ValueError("Join the challenge before submitting.")
    row = guild.get("roster", {}).get(day.isoformat(), {}).get(str(user_id))
    if row and (row.get("closed") or (now is not None and now >= datetime.fromisoformat(row["deadline"]))):
        raise ValueError("Your deadline for that challenge day has already closed.")
    report = guild.get("reports", {}).get(day.isoformat(), {})
    if report.get("complete") or report.get("published_at") or report.get("message_ids"):
        raise ValueError("That challenge day has already been reported and closed.")
    if member["last_solved"] and day < date.fromisoformat(member["last_solved"]):
        if any(not entry.get("legacy") and entry.get("date", "") > day.isoformat()
               for entry in member["submissions"]):
            raise ValueError("Only the current challenge day can accept submissions.")
        # Preserve an invalid future legacy summary, but never let it block live submissions.
        member.setdefault("future_legacy_tracking", {key: copy.deepcopy(member[key]) for key in
            ("last_solved", "streak", "longest", "completed_days")})
        known_days = sorted(key for key in member["recent"] if key <= day.isoformat() and day_count(member, key))
        member["last_solved"] = known_days[-1] if known_days else None
        member["completed_days"] = sum(key >= member["joined"] for key in known_days)
        streak = 0
        previous = None
        for key in known_days:
            candidate = date.fromisoformat(key)
            streak = streak + 1 if previous == candidate - timedelta(days=1) else 1
            previous = candidate
        member["streak"] = streak
    if difficulty is not None and difficulty not in DIFFICULTIES:
        raise ValueError("Choose Easy, Medium, or Hard, or omit difficulty.")
    for previous in member["submissions"]:
        if submission_id and str(previous.get("submission_id")) == str(submission_id):
            raise ValueError("Already counted this submission.")
        if previous.get("slug") == slug and previous.get("date") == day.isoformat():
            raise ValueError("Already counted that problem for this challenge day.")
    declared = difficulty is not None
    difficulty = difficulty or DIFFICULTY.get(slug, "Unknown")
    try:
        quest = daily_quest(guild_id, day, guild)
    except ValueError:
        quest = None
    state = member["recent"].setdefault(day.isoformat(), {"counts": [0, 0, 0], "quests": 0})
    if quest is not None:
        state["quest_assigned"] = True
    first = day_count(member, day) == 0
    previous_day = date.fromisoformat(member["last_solved"]) if member["last_solved"] else None
    comeback = bool(first and previous_day and previous_day < day - timedelta(days=1))
    if difficulty in DIFFICULTIES:
        index = DIFFICULTIES.index(difficulty)
        state["counts"][index] += 1
        member["counts"][index] += 1
    else:
        state["unknown"] = state.get("unknown", 0) + 1
        member["unknown_count"] += 1
    member["solved"] += 1
    member["period_solved"] += 1
    if first:
        previous = date.fromisoformat(member["last_solved"]) if member["last_solved"] else None
        member["streak"] = member["streak"] + 1 if previous == day - timedelta(days=1) else 1
        member["longest"] = max(member["longest"], member["streak"])
        member["last_solved"] = day.isoformat()
        member["completed_days"] += 1
    completed = bool(quest and slug == quest["slug"] and not state.get("quest_complete", False)
                     and not state.get("quests", 0))
    if completed:
        state["quest_complete"] = True
        member["quests"] += 1
    timestamp = now or utcnow()
    submission = {"slug": slug, "name": title(slug), "difficulty": difficulty,
                  "difficulty_source": "declared" if declared else ("unknown" if difficulty == "Unknown" else "catalog"),
                  "date": day.isoformat(), "url": submission_url, "submission_id": submission_id,
                  "problem_url": f"https://leetcode.com/problems/{slug}/",
                  "received_at": timestamp.astimezone(UTC.utc).isoformat(),
                  "timezone": row["timezone"] if row else member["timezone"],
                  "deadline": row["deadline"] if row else deadline(day, member["timezone"]).isoformat(),
                  "quest": completed}
    member["submissions"].append(submission)
    return {"quest": completed, "first": first, "comeback": comeback, "difficulty": difficulty,
            "submission": submission, "today_count": day_count(member, day)}


def stats(guild: dict, user_id: int, today: date) -> dict | None:
    member = guild["members"].get(str(user_id))
    if member is None:
        return None
    if not member["active"] and member["left_day"]:
        today = min(today, date.fromisoformat(member["left_day"]))
    joined = date.fromisoformat(member["joined"])
    last = date.fromisoformat(member["last_solved"]) if member["last_solved"] else None
    streak = member["streak"] if last in (today, today - timedelta(days=1)) else 0
    completed_closed = member["completed_days"] - int(last == today)
    return {"streak": streak, "missed": max(0, (today - joined).days - completed_closed),
            "solved": member["solved"], "period_solved": member["period_solved"],
            "today": day_count(member, today) > 0, "today_count": day_count(member, today),
            "active": member["active"], "joined": joined, "longest": member["longest"],
            "completed_days": member["completed_days"], "quests": member["quests"],
            "counts": member["counts"], "unknown_count": member.get("unknown_count", 0),
            "days": {date.fromisoformat(key) for key in member["recent"] if key <= today.isoformat()
                     and day_count(member, key)}}


def rankings(guild: dict, now: datetime) -> list[tuple[str, dict]]:
    rows = [(key, stats(guild, int(key), member_day(member, now)))
            for key, member in guild["members"].items() if member["active"]]
    return sorted(rows, key=lambda row: (-row[1]["streak"], row[1]["missed"], -row[1]["solved"], int(row[0])))


def _eligible(member: dict, day: date) -> bool:
    value = day.isoformat()
    return any(period["start"] <= value and (period.get("end") is None or value <= period["end"])
               for period in member.get("periods", [{"start": member["joined"], "end": member.get("left_day")}]))


def set_weekly_goal(member: dict, target: int) -> None:
    if isinstance(target, bool) or not isinstance(target, int) or not 3 <= target <= 7:
        raise ValueError("Choose a weekly goal from 3 to 7 days.")
    member["weekly_goal"] = target


def _excused(member: dict, day: date, guild: dict | None = None) -> bool:
    if day.isoformat() in member.get("excused_days", []):
        return True
    if guild is not None:
        member_id = next((key for key, value in guild["members"].items() if value is member), None)
        return bool(guild.get("roster", {}).get(day.isoformat(), {}).get(member_id, {}).get("excused"))
    return False


def progress(member: dict, today: date, period: str = "week", guild: dict | None = None) -> dict:
    if period not in {"week", "month"}:
        raise ValueError("Choose week or month.")
    start = today - timedelta(days=today.weekday()) if period == "week" else today.replace(day=1)
    result = {"solves": 0, "days": 0, "eligible": 0, "quests": 0, "quest_eligible": 0,
              "partial": False, "start": start.isoformat(), "end": today.isoformat(),
              "goal": member.get("weekly_goal", 4), "goal_met": False, "goal_days": 0, "known_eligible": 0,
              "unknown_days": 0, "excused_days": 0, "total_eligible": 0}
    day = start
    while day <= today:
        key = day.isoformat()
        if _eligible(member, day):
            result["total_eligible"] += 1
            count = day_count(member, day)
            result["solves"] += count
            result["goal_days"] += int(count > 0)
            excused = _excused(member, day, guild)
            unknown = (not excused and key < member.get("coverage_start", today.isoformat())
                       and key not in member["recent"])
            if unknown:
                result["partial"] = True
                result["unknown_days"] += 1
            if excused:
                result["excused_days"] += 1
            if not unknown and not excused:
                result["eligible"] += 1
                result["known_eligible"] += 1
                result["days"] += int(count > 0)
            assigned = key in guild.get("quests", {}) if guild is not None else member["recent"].get(key, {}).get("quest_assigned", False)
            if key >= member.get("quest_start", today.isoformat()) and assigned and not unknown and not excused:
                result["quest_eligible"] += 1
                result["quests"] += int(member["recent"].get(key, {}).get("quest_complete", False))
        day += timedelta(days=1)
    result["goal_met"] = (result["goal_days"] >= result["goal"] if period == "week"
                          else progress(member, today, "week", guild)["goal_met"])
    return result


def activity_days(member: dict, today: date, weeks: int = 12) -> list[dict]:
    start = today - timedelta(days=today.weekday() + (weeks - 1) * 7)
    output = []
    for offset in range(weeks * 7):
        day = start + timedelta(days=offset)
        key = day.isoformat()
        if day > today:
            status, count = "future", None
        elif not _eligible(member, day):
            status, count = "inactive", None
        elif key < member.get("coverage_start", today.isoformat()) and key not in member["recent"]:
            status, count = "unknown", None
        else:
            status, count = "active", day_count(member, day)
        output.append({"date": key, "count": count, "status": status})
    return output


def daily_report(guild: dict, day: date, zone: str | None = None) -> tuple[list[str], list[str]]:
    done, pending = [], []
    for key, member in guild["members"].items():
        if not _eligible(member, day) or (zone and member["timezone"] != zone):
            continue
        (done if day_count(member, day) else pending).append(key)
    return done, pending


def reminder_due(guild: dict, member: dict, now: datetime) -> str | None:
    if not guild["reminders_enabled"] or not member["active"] or not member["reminders"]:
        return None
    day = member_day(member, now)
    if _excused(member, day, guild):
        return None
    if member.get("snoozed_until") and now < datetime.fromisoformat(member["snoozed_until"]):
        return None
    row = guild.get("roster", {}).get(day.isoformat(), {}).get(next(
        (key for key, value in guild["members"].items() if value is member), ""))
    if row and (row.get("closed") or now >= datetime.fromisoformat(row["deadline"])):
        return None
    if day_count(member, day):
        return None
    if member.get("snoozed_until") and member.get("snoozed_day") == day.isoformat():
        elapsed = (now.astimezone(UTC.utc) - datetime.fromisoformat(member["snoozed_until"])).total_seconds()
        slot = f"snooze@{member['snoozed_until']}"
        if (0 <= elapsed < 3600 and member["reminder_marks"].get(slot) != day.isoformat()
                and member["reminder_marks"].get(f"{day.isoformat()}:{slot}") != day.isoformat()):
            return slot
    slots = reminder_slots(guild, member)
    pending = member.get("pending_timezone")
    zone = pending["timezone"] if pending and now >= datetime.fromisoformat(pending["effective_at"]) else member["timezone"]
    for slot in slots:
        hour, minutes = map(int, slot.split(":"))
        reminder_date = day if time(hour, minutes) >= CUTOFF else day + timedelta(days=1)
        scheduled = datetime.combine(reminder_date, time(hour, minutes), timezone(zone))
        # A nonexistent wall time round-trips to a different wall time: skip it.
        if scheduled.astimezone(UTC.utc).astimezone(scheduled.tzinfo).replace(tzinfo=None) != scheduled.replace(tzinfo=None):
            continue
        elapsed = (now.astimezone(UTC.utc) - scheduled.astimezone(UTC.utc)).total_seconds()
        if (0 <= elapsed < 3600 and member["reminder_marks"].get(slot) != day.isoformat()
                and member["reminder_marks"].get(f"{day.isoformat()}:{slot}") != day.isoformat()):
            return slot
    return None


def _period_zone(member: dict, day: date) -> str:
    # The first enrollment covering a date owns its deadline across same-day rejoins.
    for period in member.get("periods", []):
        if period["start"] <= day.isoformat() and (period.get("end") is None or day.isoformat() <= period["end"]):
            return period.get("timezone", member["timezone"])
    return member["timezone"]


def sync_roster(guild: dict, now: datetime, guild_id: int | None = None) -> None:
    """Create immutable deadline snapshots and close each participant at their cutoff."""
    guild.setdefault("roster", {})
    guild.setdefault("reports", {})
    for key, member in guild["members"].items():
        upgrade_member(member, member_day(member, now))
        apply_pending_timezone(guild, int(key), now)
        today = member_day(member, now)
        # coverage_start is the deployment boundary, not the join date; no invented old reports.
        start = (date.fromisoformat(member["roster_cursor"]) + timedelta(days=1)
                 if member.get("roster_cursor") else date.fromisoformat(member.get("roster_start", today.isoformat())))
        member.setdefault("roster_start", today.isoformat())
        cursor = start
        while cursor <= today:
            label = cursor.isoformat()
            report = guild["reports"].get(label, {})
            published = report.get("complete") or report.get("published_at") or report.get("message_ids")
            if _eligible(member, cursor) and not published:
                if guild_id is not None:
                    try:
                        daily_quest(guild_id, cursor, guild)
                    except ValueError:
                        pass  # Tracking/reporting continues when the catalog is unconfigured.
                roster = guild["roster"].setdefault(label, {})
                zone = _period_zone(member, cursor)
                roster.setdefault(key, {"timezone": zone,
                    "deadline": deadline(cursor, zone).isoformat(),
                    "excused": not member["active"] and label == member.get("left_day"),
                    "closed": False, "count": 0, "quest": False, "links": []})
            cursor += timedelta(days=1)
        member["roster_cursor"] = max(member.get("roster_cursor", today.isoformat()), today.isoformat())
        # A newly configured catalog can assign today's quest even after today's roster was made.
        current_report = guild["reports"].get(today.isoformat(), {})
        published = current_report.get("complete") or current_report.get("published_at") or current_report.get("message_ids")
        if _eligible(member, today) and not published:
            # A pause may advance the cursor through today before a same-day rejoin.
            # Materialize today's newly eligible participant even when no date needs catch-up.
            zone = _period_zone(member, today)
            guild["roster"].setdefault(today.isoformat(), {}).setdefault(key, {
                "timezone": zone, "deadline": deadline(today, zone).isoformat(),
                "excused": not member["active"] and today.isoformat() == member.get("left_day"),
                "closed": False, "count": 0, "quest": False, "links": []})
        if guild_id is not None and _eligible(member, today) and not published:
            try:
                daily_quest(guild_id, today, guild)
            except ValueError:
                pass
    for label, roster in guild["roster"].items():
        for key, row in roster.items():
            if row["closed"]:
                continue
            member = guild["members"].get(key)
            if member is None:
                continue
            row["count"] = day_count(member, label)
            row["quest"] = bool(member["recent"].get(label, {}).get("quest_complete", False))
            row["links"] = [entry["url"] for entry in member["submissions"]
                            if entry.get("date") == label and entry.get("url")]
            if now >= datetime.fromisoformat(row["deadline"]):
                row["closed"] = True


def due_reports(guild: dict, now: datetime) -> list[tuple[date, dict]]:
    sync_roster(guild, now)
    return [(date.fromisoformat(label), roster) for label, roster in sorted(guild["roster"].items())
            if roster and not guild["reports"].get(label, {}).get("complete", False)
            and now >= max(datetime.fromisoformat(row["deadline"]) for row in roster.values())]


def migrate_legacy(source: dict, now: datetime) -> dict[str, dict]:
    """Preserve raw entries and their original labels; derive counts without deleting details."""
    if source.get("version") != 1 or not isinstance(source.get("guilds"), dict):
        raise ValueError("Unsupported legacy JSON. Expected a version 1 tracker file.")
    converted = {}
    for guild_id, old in source["guilds"].items():
        int(guild_id)
        guild = new_guild()
        guild["timezone"] = timezone(old.get("timezone", guild["timezone"])).key
        for field in ("report_channel_id", "reminders_enabled", "reminder_times"):
            if field in old:
                guild[field] = copy.deepcopy(old[field])
        for user_id, old_member in old.get("members", {}).items():
            int(user_id)
            zone = timezone(old_member.get("timezone", guild["timezone"])).key
            today = challenge_day(now, zone)
            joined = date.fromisoformat(old_member["joined"])
            member = new_member(joined, zone)
            member["legacy_member"] = copy.deepcopy(old_member)
            member["reminder_time"] = old_member.get("reminder_time")
            member["late_reminder"] = old_member.get("late_reminder", False)
            member["active"] = old_member.get("active", True)
            member["reminders"] = old_member.get("reminders", True)
            member["left_day"] = old_member.get("left_day") or (today.isoformat() if not member["active"] else None)
            member["periods"][0]["end"] = member["left_day"]
            member["quest_start"] = today.isoformat()
            member["roster_start"] = today.isoformat()
            entries = [copy.deepcopy(entry) for entry in old.get("submissions", [])
                       if str(entry["user_id"]) == str(user_id)]
            days = set()
            quest_days = set()
            for entry in entries:
                label = entry["date"]
                day = date.fromisoformat(label)  # validation, never relabel historical activity
                slug = entry.get("slug")
                difficulty = entry.get("difficulty") or DIFFICULTY.get(slug, "Unknown")
                state = member["recent"].setdefault(label, {"counts": [0, 0, 0], "quests": 0})
                if difficulty in DIFFICULTIES:
                    index = DIFFICULTIES.index(difficulty)
                    state["counts"][index] += 1
                    member["counts"][index] += 1
                    if entry.get("quest_xp", 0):
                        state["quests"] |= 1 << index
                else:
                    member["unknown_count"] += 1
                    state["unknown"] = state.get("unknown", 0) + 1
                member["solved"] += 1
                if day >= joined and day <= today:
                    member["period_solved"] += 1
                    days.add(label)
                    if entry.get("quest_xp", 0):
                        quest_days.add(label)
                member["submissions"].append({**entry, "legacy": True, "raw": copy.deepcopy(entry),
                    "difficulty": difficulty, "name": title(slug) if slug else "Unknown problem"})
            member["coverage_start"] = min([joined.isoformat()] + list(member["recent"]))
            member["completed_days"] = len(days)
            previous = None
            for label in sorted(days):
                day = date.fromisoformat(label)
                member["streak"] = member["streak"] + 1 if previous == day - timedelta(days=1) else 1
                member["longest"] = max(member["longest"], member["streak"])
                previous = day
            member["last_solved"] = max(days) if days else None
            member["quests"] = len(quest_days)
            member["xp"] = (len({entry.get("slug") for entry in entries if entry["date"] >= joined.isoformat()}) * 50
                            + len(days) * 25 + len(quest_days) * 50)
            member["reported_day"] = (today - timedelta(days=1)).isoformat()
            guild["members"][str(user_id)] = member
        guild["legacy_imported"] = True
        converted[str(guild_id)] = guild
    return converted
