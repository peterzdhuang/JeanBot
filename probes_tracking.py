"""Synthetic retention probes. Run: python probes_tracking.py

Uses in-memory fixtures only: no Discord login, environment file, database, or
network. Assertions establish observed behavior; retention risks are hypotheses,
not measured effects on real users. These probes do not change runtime code.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone

import tracker

MONDAY = date(2026, 10, 19)
QUEST = {"slug": "add-two-numbers", "name": "Add Two Numbers", "difficulty": "Medium",
         "url": "https://leetcode.com/problems/add-two-numbers/", "catalog_version": "probe-fixture"}


def at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=hour)


def guild(start: date = MONDAY, zone: str = "UTC") -> dict:
    data = tracker.new_guild()
    data["timezone"] = zone
    tracker.enroll(data, 1, start, zone)
    return data


def solve(data: dict, day: date, slug: str = "two-sum", submission_id: str | None = None) -> dict:
    data["quests"].setdefault(day.isoformat(), dict(QUEST))
    attempt = submission_id or str(1000 + len(data["members"]["1"]["submissions"]))
    return tracker.record(data, 1, day, slug, guild_id=7, now=at(day), submission_id=attempt,
                          submission_url=f"https://leetcode.com/problems/{slug}/submissions/{attempt}/")


def missed_day_recovery() -> dict:
    data = guild()
    for offset in range(7):
        solve(data, MONDAY + timedelta(days=offset))
    open_day = MONDAY + timedelta(days=7)
    grace = tracker.stats(data, 1, open_day)
    after_miss = tracker.stats(data, 1, open_day + timedelta(days=1))
    comeback = solve(data, open_day + timedelta(days=1))
    assert comeback["comeback"] is True
    returned = tracker.stats(data, 1, open_day + timedelta(days=1))
    assert (grace["streak"], after_miss["streak"], returned["streak"], returned["longest"], returned["solved"]) == (7, 0, 1, 7, 8)
    assert len(data["members"]["1"]["submissions"]) == 8
    return {
        "observed": {"open_day_streak": grace["streak"], "streak_after_miss": after_miss["streak"],
                     "return_streak": returned["streak"], "best_streak": returned["longest"], "lifetime_solves": returned["solved"],
                     "comeback_acknowledged": comeback["comeback"]},
        "risk_hypothesis": "Raw streaks remain truthful (seven to zero to one); comeback recognition now explicitly acknowledges the return. Best streak and solve history remain intact. Its effect on returning members still needs measurement.",
    }


def weekly_target_support() -> dict:
    data = guild()
    for offset in (0, 1, 3, 6):
        solve(data, MONDAY + timedelta(days=offset))
    member = data["members"]["1"]
    result = tracker.progress(member, MONDAY + timedelta(days=6), guild=data)
    target_keys = [key for key in member if "goal" in key or "target" in key]
    assert (result["days"], result["eligible"]) == (4, 7)
    assert target_keys == ["weekly_goal"]
    assert result["goal"] == 4 and result["goal_met"] is True
    return {
        "observed": {"participation_days": result["days"], "eligible_days": result["eligible"],
                     "goal_or_target_state_fields": target_keys, "weekly_goal": result["goal"],
                     "goal_days": result["goal_days"], "goal_met": result["goal_met"], "progress_result_keys": sorted(result)},
        "risk_hypothesis": "The same four practice days now satisfy a configurable four-day weekly goal while retaining the factual 4/7 participation ratio. This addresses a demonstrated missing goal signal; retention impact is unmeasured.",
    }


def repeat_practice_credit() -> dict:
    data = guild()
    solve(data, MONDAY, submission_id="111")
    before = copy.deepcopy(data["members"]["1"])
    rejection = None
    try:
        solve(data, MONDAY, submission_id="222")
    except ValueError as error:
        rejection = str(error)
    assert rejection is not None
    assert data["members"]["1"] == before
    solve(data, MONDAY + timedelta(days=1), submission_id="333")
    member = data["members"]["1"]
    assert member["solved"] == 2 and len(member["submissions"]) == 2
    return {
        "observed": {"second_same_day_attempt": rejection, "history_unchanged_after_rejection": member["submissions"][0] == before["submissions"][0],
                     "next_day_same_problem_accepted": True, "counted_solves": member["solved"]},
        "risk_hypothesis": "A legitimate same-day review attempt receives no additional counted activity and is not stored in history. This is the specified duplicate-count policy, but can frustrate users expecting a practice-attempt diary.",
    }


def timezone_change_cost() -> dict:
    data = guild()
    for offset in range(7):
        solve(data, MONDAY + timedelta(days=offset))
    day = MONDAY + timedelta(days=6)
    before = tracker.stats(data, 1, day)
    records = copy.deepcopy(data["members"]["1"]["submissions"])
    pending = tracker.change_timezone(data, 1, "America/Vancouver", at(day))
    member = data["members"]["1"]
    assert member["timezone"] == "UTC"
    cutoff = datetime.fromisoformat(pending["effective_at"])
    tracker.sync_roster(data, cutoff)
    after = tracker.stats(data, 1, tracker.member_day(member, cutoff))
    assert (before["streak"], after["streak"], after["longest"], after["solved"]) == (7, 7, 7, 7)
    assert member["submissions"] == records
    return {
        "observed": {"timezone_change_scheduled": pending, "streak_before": before["streak"],
                     "streak_after_transition": after["streak"], "best_streak": after["longest"],
                     "preserved_submission_records": len(member["submissions"]), "rejoin_required": False},
        "risk_hypothesis": "A future-effective timezone setting now preserves all seven current streak days and all seven submission records without leaving/rejoining. New timezone labels cannot run backwards during a westward transition. Travel-related friction is reduced in this scenario; real usage still needs observation.",
    }


def incomplete_legacy_denominators() -> dict:
    today = MONDAY + timedelta(days=6)
    data = guild(date(2026, 10, 1))
    member = data["members"]["1"]
    member["coverage_start"] = today.isoformat()
    solve(data, today)
    result = tracker.progress(member, today, guild=data)
    cells = {cell["date"]: cell for cell in tracker.activity_days(member, today)}
    unknown_days = [day for day in cells if MONDAY.isoformat() <= day <= today.isoformat() and cells[day]["status"] == "unknown"]
    assert result["partial"] and (result["days"], result["eligible"]) == (1, 1)
    assert (result["total_eligible"], result["unknown_days"]) == (7, 6)
    assert len(unknown_days) == 6
    return {
        "observed": {"weekly_days": result["days"], "weekly_denominator": result["eligible"],
                     "total_calendar_eligible": result["total_eligible"], "unknown_days_excluded": result["unknown_days"],
                     "partial_history_flag": result["partial"], "unknown_grid_days_in_week": len(unknown_days)},
        "risk_hypothesis": "The denominator now reads 1/1 known eligible days and separately preserves six unknown days within seven calendar-eligible days. Unknown history is not treated as missed participation; presentation must keep the coverage caveat visible.",
    }


def inactive_reminders_and_reports() -> dict:
    data = guild()
    solve(data, MONDAY)
    tuesday = MONDAY + timedelta(days=1)
    tracker.leave_member(data, 1, at(tuesday))
    member = data["members"]["1"]
    reminder = tracker.reminder_due(data, member, at(tuesday, 20))
    reports = tracker.due_reports(data, tracker.deadline(tuesday, "UTC"))
    closed_row = next(roster["1"] for day, roster in reports if day == tuesday)
    result = tracker.progress(member, tuesday, guild=data)
    assert reminder is None and closed_row["excused"] and closed_row["closed"]
    assert (result["days"], result["eligible"]) == (1, 1)
    assert (result["total_eligible"], result["excused_days"]) == (2, 1)
    return {
        "observed": {"reminder_after_leave": reminder, "leave_day_report_excused": closed_row["excused"],
                     "leave_day_report_solve_count": closed_row["count"], "weekly_days": result["days"], "weekly_denominator": result["eligible"],
                     "excused_days_excluded": result["excused_days"], "calendar_eligible_days": result["total_eligible"]},
        "risk_hypothesis": "Paused members receive no reminder, their leave day is excused in the report, and the same excused day is excluded from weekly progress (1/1 known eligible). This resolves the demonstrated cross-view inconsistency.",
    }


def run_probes() -> dict:
    cases = [missed_day_recovery, weekly_target_support, repeat_practice_credit,
             timezone_change_cost, incomplete_legacy_denominators, inactive_reminders_and_reports]
    evidence = []
    for case in cases:
        evidence.append({"probe": case.__name__, "assertions": "passed", **case()})
    return {"scope": "synthetic, in-memory tracker behavior only; no production users, database, Discord, or network",
            "retention_measurement": "No real retention, activation, churn, or reminder-open metrics are available from these probes.",
            "passed": len(evidence), "probes": evidence}


if __name__ == "__main__":
    print(json.dumps(run_probes(), indent=2))
