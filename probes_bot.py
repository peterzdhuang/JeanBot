"""Reproducible, synthetic Discord launch/retention probes.

Runs actual bot callbacks through test_bot's AST transport harness. No bot login,
network, live database, or credential loading occurs. Measurements describe UX
behavior, not causal effects on real-user retention. Run: python3 probes_bot.py
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from test_bot import FakeResponse, MemoryStore, command_namespace, component_method
from tracker import new_guild, new_member, record, stats


class Probe:
    def __init__(self, now: datetime, guild=None):
        self.now = now
        self.ns = command_namespace()
        self.ns["utcnow"] = lambda: self.now
        self.store = MemoryStore(guild)
        self.channel = types.SimpleNamespace(send=AsyncMock(return_value=types.SimpleNamespace(id=123)))
        self.bot = types.SimpleNamespace(
            store=self.store, submit_lock=asyncio.Lock(), get_channel=lambda channel_id: self.channel,
            find_report_message=AsyncMock(return_value=None))
        self.ns["bot"] = self.bot

    def interaction(self, user_id=1):
        return types.SimpleNamespace(
            guild_id=7, user=types.SimpleNamespace(id=user_id, display_name=f"Synthetic member {user_id}",
                display_avatar=types.SimpleNamespace(url="https://example.com/avatar.png")),
            response=FakeResponse(), followup=types.SimpleNamespace(send=AsyncMock()))

    async def tick(self):
        await self.ns["process_guild"](self.bot, 7)


def embed_text(interaction):
    embed = interaction.followup.send.call_args.kwargs["embed"]
    return embed.values["description"] + "\n" + "\n".join(
        f"{field['name']}: {field['value']}" for field in embed.fields)


def dashboard_buttons():
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text())
    view = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DashboardView")
    return [keyword.value.value for node in view.body if isinstance(node, ast.AsyncFunctionDef)
            for decorator in node.decorator_list if isinstance(decorator, ast.Call)
            for keyword in decorator.keywords if keyword.arg == "label" and isinstance(keyword.value, ast.Constant)]


async def onboarding_probe():
    probe = Probe(datetime(2026, 10, 10, 18, tzinfo=timezone.utc))
    interaction = probe.interaction()
    await probe.ns["join"](interaction, "UTC")
    text = embed_text(interaction)
    result = {
        "probe": "first_join_onboarding",
        "response": text,
        "ephemeral": interaction.followup.send.call_args.kwargs["ephemeral"],
        "reminders_default_on": probe.store.data["members"]["1"]["reminders"],
        "actual_problem_link_in_join_response": "https://leetcode.com/problems/" in text,
        "persistent_button_labels": dashboard_buttons(),
        "finding": "Join includes the actual daily problem link, weekly goal, timezone/deadline, "
                   "reminder controls, and instructions for copying the accepted attempt URL. "
                   "Submit solve opens a form without another typed command.",
    }
    assert result["reminders_default_on"] and result["actual_problem_link_in_join_response"]
    assert {"Submit solve", "Snooze 1h", "Reminders"} <= set(result["persistent_button_labels"])
    return result


async def submission_probe():
    cases = [
        ("problem_only", "https://leetcode.com/problems/two-sum/", None),
        ("problem_scoped_attempt", "https://leetcode.com/problems/two-sum/submissions/12345/", None),
        ("global_attempt_without_problem", "https://leetcode.com/submissions/detail/12345/", None),
        ("global_attempt_plus_problem", "https://leetcode.com/submissions/detail/12345/",
         "https://leetcode.com/problems/two-sum/"),
    ]
    outcomes = []
    for name, url, problem_url in cases:
        probe = Probe(datetime(2026, 10, 10, 18, tzinfo=timezone.utc))
        interaction = probe.interaction()
        await probe.ns["submit"](interaction, url, problem_url=problem_url)
        count = probe.store.data["members"].get("1", {}).get("solved", 0)
        outcomes.append({"case": name, "counted": count,
                         "error": interaction.response.messages[0] if interaction.response.messages else None,
                         "response_has_submission_link": bool(count and "View submission" in embed_text(interaction))})
    assert [case["counted"] for case in outcomes] == [0, 1, 0, 1]
    modal_probe = Probe(datetime(2026, 10, 10, 18, tzinfo=timezone.utc))
    interaction = modal_probe.interaction()
    modal = types.SimpleNamespace(
        attempt_url=types.SimpleNamespace(value="https://leetcode.com/submissions/detail/12345/"),
        problem_url=types.SimpleNamespace(value="https://leetcode.com/problems/two-sum/"),
        difficulty=types.SimpleNamespace(value=""))
    await component_method(modal_probe.ns, "SubmitModal", "on_submit")(modal, interaction)
    assert modal_probe.store.data["members"]["1"]["solved"] == 1
    return {"probe": "submission_link_friction", "cases": outcomes,
            "modal_global_attempt_with_problem_counted": 1,
            "finding": "Problem-only URLs are rejected. A global attempt URL requires a second problem_url field; "
                       "the Submit solve form provides both fields. A problem-scoped attempt works with one URL. "
                       "The actual attempt link is retained in both paths."}


async def reminder_burst_probe():
    guild = new_guild()
    guild["report_channel_id"] = 9
    guild["members"] = {str(user_id): new_member(date(2026, 10, 10), "UTC") for user_id in range(1, 101)}
    probe = Probe(datetime(2026, 10, 10, 20, tzinfo=timezone.utc), guild)
    await probe.tick()
    first_count = probe.channel.send.await_count
    first_messages = [call.args[0] for call in probe.channel.send.call_args_list]
    await probe.tick()
    repeat_count = probe.channel.send.await_count - first_count
    probe.now = datetime(2026, 10, 11, 2, 15, tzinfo=timezone.utc)
    await probe.tick()
    second_count = probe.channel.send.await_count - first_count
    assert (first_count, repeat_count, second_count) == (2, 0, 0)
    assert all("Opt out:" in message for message in first_messages)
    return {"probe": "one_hundred_pending_reminders", "members": 100, "timezone": "UTC",
            "messages_at_local_20_00": first_count, "duplicate_messages_on_same_tick": repeat_count,
            "additional_messages_at_local_02_15": second_count,
            "messages_per_full_pending_day": first_count + second_count,
            "each_message_includes_opt_out_instruction": True,
            "finding": "100 new pending members receive two batches at 20:00 and no default late reminder. "
                       "Repeated scheduler ticks do not resend successful batches. "
                       "Transport is mocked, so this measures message count, not real rate-limit latency."}


async def optout_probe():
    guild = new_guild()
    guild["report_channel_id"] = 9
    guild["members"] = {str(user_id): new_member(date(2026, 10, 10), "UTC") for user_id in range(1, 11)}
    probe = Probe(datetime(2026, 10, 10, 20, tzinfo=timezone.utc), guild)
    for user_id in (1, 2, 3):
        await probe.ns["reminders"](probe.interaction(user_id), False)
    record(probe.store.data, 4, date(2026, 10, 10), "two-sum", guild_id=7, now=probe.now,
           submission_url="https://leetcode.com/problems/two-sum/submissions/444/", submission_id="444")
    await probe.tick()
    messages = [call.args[0] for call in probe.channel.send.call_args_list]
    visible = sorted(set(re.findall(r"<@(\d+)>", "\n".join(messages))), key=int)
    permitted = sorted({str(user.id) for call in probe.channel.send.call_args_list
                        for user in call.kwargs["allowed_mentions"].values["users"]}, key=int)
    assert len(messages) == 1
    assert visible == permitted == [str(user_id) for user_id in range(5, 11)]
    return {"probe": "opt_out_and_completed_filter", "members": 10, "opted_out": 3, "already_submitted": 1,
            "reminder_messages": len(messages), "excluded_members_pinged": 0,
            "visible_member_ids": visible, "allowed_mention_member_ids": permitted,
            "dashboard_has_snooze_or_reminder_toggle_button": any(
                term in label.lower() for label in dashboard_buttons() for term in ("snooze", "reminder")),
            "finding": "Personal opt-out works immediately and submitted members are excluded. "
                       "The dashboard exposes Snooze 1h and Reminders; personal time and optional late pings are configurable. "
                       "Only the six intended pending members are allowed mentions in the single batch."}


async def public_report_probe():
    guild = new_guild()
    guild["report_channel_id"] = 9
    guild["members"] = {str(user_id): new_member(date(2026, 10, 10), "UTC") for user_id in range(1, 4)}
    record(guild, 1, date(2026, 10, 10), "two-sum", guild_id=7,
           now=datetime(2026, 10, 10, 18, tzinfo=timezone.utc),
           submission_url="https://leetcode.com/problems/two-sum/submissions/111/", submission_id="111")
    probe = Probe(datetime(2026, 10, 11, 4, tzinfo=timezone.utc), guild)
    await probe.tick()
    reports = [call for call in probe.channel.send.call_args_list if "lc-report:" in call.args[0]]
    report_text = "\n".join(call.args[0] for call in reports)
    assert "Submitted **1**" in report_text and "Missed **2**" in report_text
    assert "<@2>" in report_text and "<@3>" in report_text
    assert all(call.kwargs["allowed_mentions"] == "none" for call in reports)
    return {"probe": "public_missed_report", "report_messages": len(reports),
            "submitted": 1, "missed": 2, "missed_member_identifiers_visible": True,
            "notification_mentions_enabled": False,
            "report_excerpt": report_text,
            "finding": "The configured channel sees each missed member by name/mention rendering, without notification pings. "
                       "This matches the requested accountability report; whether visibility discourages return is a user-research question."}


async def comeback_probe():
    guild = new_guild()
    guild["members"]["1"] = new_member(date(2026, 10, 1), "UTC")
    for offset in range(7):
        day = date(2026, 10, 1) + timedelta(days=offset)
        record(guild, 1, day, "two-sum", guild_id=7,
               now=datetime(day.year, day.month, day.day, 18, tzinfo=timezone.utc),
               submission_url=f"https://leetcode.com/problems/two-sum/submissions/{100 + offset}/",
               submission_id=str(100 + offset))
    probe = Probe(datetime(2026, 10, 9, 18, tzinfo=timezone.utc), guild)
    before = stats(probe.store.data, 1, date(2026, 10, 9))
    interaction = probe.interaction()
    await probe.ns["submit"](interaction, "https://leetcode.com/problems/two-sum/submissions/999/")
    after = stats(probe.store.data, 1, date(2026, 10, 9))
    assert (before["streak"], after["streak"], after["longest"], after["solved"]) == (0, 1, 7, 8)
    text = embed_text(interaction)
    assert "This week" in text and "Daily activity" in text
    assert "Welcome back" in interaction.followup.send.call_args.kwargs["embed"].values["title"]
    return {"probe": "return_after_one_missed_day", "prior_streak": 7, "missed_days": 1,
            "displayed_streak_before_return": before["streak"], "displayed_streak_after_return": after["streak"],
            "best_streak_retained": after["longest"], "lifetime_solves_retained": after["solved"],
            "daily_records_retained": len(probe.store.data["members"]["1"]["recent"]),
            "success_title": interaction.followup.send.call_args.kwargs["embed"].values["title"],
            "weekly_progress_and_grid_present": True,
            "finding": "The streak correctly resets from 7 to 0 after a missed day, then to 1 on return; "
                       "best streak, lifetime total, history, weekly progress, and grid remain. "
                       "The success title welcomes the member back and confirms that previous solves and best streak still count."}


async def run():
    return {"mode": "synthetic_only_no_live_services", "retention_effects_measured": False,
            "probes": [await probe() for probe in (
                onboarding_probe, submission_probe, reminder_burst_probe, optout_probe,
                public_report_probe, comeback_probe)]}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
