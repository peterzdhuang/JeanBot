"""Discord LeetCode tracker: durable history, personal deadlines, and daily progress."""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from activity_grid import render_activity
from catalog import title
from storage import PostgresStore
from scheduler import process_guild as process_scheduled_guild
from tracker import (
    accept_buddy, apply_pending_timezone, buddy_progress, challenge_day, change_timezone,
    daily_quest, day_count, deadline, enroll, invite_buddy, leave_member, member_day,
    progress as period_progress, rankings, record, reminder_slots, remove_buddy, set_reminder_preferences,
    set_weekly_goal, snooze_member, stats, submission_from_url, sync_roster, timezone, utcnow,
)

load_dotenv(Path(__file__).resolve().with_name(".env"))
log = logging.getLogger("arena")
PURPLE, GREEN, GOLD, MUTED = 0x8B5CF6, 0x2DD4BF, 0xFBBF24, 0x64748B
Difficulty = Literal["Easy", "Medium", "Hard"]
Period = Literal["week", "month"]
BuddyAction = Literal["invite", "accept", "remove", "status"]


def log_failure(context: str, error: Exception) -> Exception:
    """Keep useful tracebacks while redacting configured credentials."""
    original = getattr(error, "original", error)
    details = "".join(traceback.format_exception(type(original), original, original.__traceback__))
    secrets = [os.getenv("DISCORD_TOKEN", ""), os.getenv("DATABASE_URL", "")]
    try:
        password = urlsplit(os.getenv("DATABASE_URL", "")).password
        if password:
            secrets.extend((password, unquote(password)))
    except ValueError:
        pass
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            details = details.replace(secret, "[REDACTED]")
    log.error("%s (%s):\n%s", context, type(original).__name__, details.rstrip())
    return original


def card(name: str, description: str = "", color: int = PURPLE, user=None) -> discord.Embed:
    embed = discord.Embed(title=name, description=description, color=color, timestamp=utcnow())
    embed.set_footer(text="LeetCode • personal challenge days close at 3:15 AM")
    if user is not None:
        embed.set_author(name=user.display_name[:256], icon_url=str(user.display_avatar.url))
    return embed


def player(data: dict, user_id: int) -> dict:
    member = data["members"].get(str(user_id))
    if not member:
        raise ValueError("Join first: /join timezone_name:PST")
    return member


def player_day(data: dict, user_id: int, now):
    member = data["members"].get(str(user_id))
    return member_day(member, now) if member else challenge_day(now, data["timezone"])


def player_deadline(data: dict, user_id: int, now):
    member = data["members"].get(str(user_id))
    if member is not None:
        apply_pending_timezone(data, user_id, now)
    day = player_day(data, user_id, now)
    row = data.get("roster", {}).get(day.isoformat(), {}).get(str(user_id))
    if row and row.get("deadline"):
        return datetime.fromisoformat(row["deadline"])
    return deadline(day, member["timezone"] if member else data["timezone"])


def count_day(member: dict, day: date) -> int:
    return day_count(member, day)


def quest_done(member: dict, day: date) -> bool:
    bucket = member.get("recent", {}).get(day.isoformat(), {})
    return bool(bucket.get("quest_complete", False))


def progress_text(member: dict, day: date, period: str, guild: dict | None = None) -> str:
    result = period_progress(member, day, period, guild=guild)
    if period == "week":
        text = (f"**{result.get('goal_days', result['days'])}/{result.get('goal', member.get('weekly_goal', 4))} days/week** · "
                f"**{result['solves']}** solves")
        if result.get("goal_met"):
            text += " · Weekly goal reached!"
    else:
        text = f"**{result.get('goal_days', result['days'])}** days submitted · **{result['solves']}** solves"
    text += f"\n**{result['quests']}/{result['quest_eligible']}** assigned quests completed"
    text += (f"\nKnown eligible days: {result.get('known_eligible', result['eligible'])} · "
             f"Unknown: {result.get('unknown_days', 0)} · Excused: {result.get('excused_days', 0)}")
    if result.get("partial") or result.get("unknown_days"):
        text += "\nPartial history: unknown days are excluded from participation rates."
    return text


def add_buddy_progress(embed, data: dict, user_id: int, now):
    rows = buddy_progress(data, user_id, now)
    if rows:
        lines = [f"<@{row['user_id']}> · {row['today_count']} solves today · {row['streak']} day streak · "
                 f"{row['week'].get('goal_days', row['week']['days'])}/{row['week'].get('goal', 4)} days this week"
                 for row in rows[:10]]
        if len(rows) > 10:
            lines.append(f"{len(rows) - 10} more connections · `/buddy action:status` shows every buddy.")
        for index, page in enumerate(split_lines(lines, 1000), 1):
            embed.add_field(name="Your study buddies" if index == 1 else "Study buddies continued", value=page,
                            inline=False)


def add_progress(embed, data: dict, user_id: int, now, *, periods=("week", "month")):
    player(data, user_id)
    apply_pending_timezone(data, user_id, now)
    member = player(data, user_id)
    day = player_day(data, user_id, now)
    result = stats(data, user_id, day)
    embed.add_field(name="Today / streak", value=(f"**{count_day(member, day)}** solves today · "
                    f"**{result['streak']}** day streak · best **{result['longest']}** days"), inline=False)
    for period in periods:
        embed.add_field(name=f"This {period}", value=progress_text(member, day, period, guild=data), inline=False)
    embed.add_field(name="Lifetime", value=f"**{member['solved']:,}** solves", inline=False)
    start = day - timedelta(days=day.weekday() + 77)
    end = day + timedelta(days=6 - day.weekday())
    embed.add_field(name=f"Daily activity · {start} – {end}", value=(
        "One square per challenge day; greener = 1 / 2 / 3 / 4+ solves. "
        "Empty = zero; gray = unknown; blue-gray = inactive; dark = future."), inline=False)
    add_buddy_progress(embed, data, user_id, now)


def profile_embed(data: dict, target, now) -> discord.Embed:
    player(data, target.id)
    apply_pending_timezone(data, target.id, now)
    member = player(data, target.id)
    day = player_day(data, target.id, now)
    embed = card("LeetCode progress", f"{'Active' if member['active'] else 'Tracking paused'} · "
                 f"**{day}** · {member['timezone']}\n"
                 f"Deadline: <t:{int(player_deadline(data, target.id, now).timestamp())}:F>\n"
                 f"Today's quest: {'completed' if quest_done(member, day) else 'not completed'}\n"
                 f"Reminders: **{'on' if member['reminders'] else 'off'}** · `/reminders` for settings",
                 user=target)
    add_progress(embed, data, target.id, now)
    counts = member.get("counts", [0, 0, 0])
    embed.add_field(name="Recorded difficulty totals", value=" · ".join(
        f"{label}: {value}" for label, value in zip(("Easy", "Medium", "Hard"), counts))
        + f" · Unknown: {member.get('unknown_count', 0)}", inline=False)
    return embed


def split_lines(lines: list[str], limit: int = 1800) -> list[str]:
    """Paginate every line rather than silently dropping members or history."""
    pages, current = [], ""
    for line in lines:
        # A pathological title cannot make a Discord message exceed its limit.
        for start in range(0, max(1, len(line)), limit):
            piece = line[start:start + limit]
            if current and len(current) + len(piece) + 1 > limit:
                pages.append(current)
                current = ""
            current += ("\n" if current else "") + piece
    if current:
        pages.append(current)
    return pages or ["No activity yet."]


def leaderboard_embed(data: dict, now, page: int = 1) -> discord.Embed:
    rows = rankings(data, now)
    pages = max(1, (len(rows) + 14) // 15)
    if not 1 <= page <= pages:
        raise ValueError(f"Choose a page from 1 to {pages}.")
    lines = [f"**#{index}** <@{key}> · **{result['streak']}d** streak · "
             f"{result['missed']} missed · {result['solved']:,} solves"
             for index, (key, result) in enumerate(rows, 1)][(page - 1) * 15:page * 15]
    return card(f"LeetCode leaderboard · {page}/{pages}", "\n".join(lines)
                or "No active members. Join with /join.", GOLD)


def today_pages(data: dict, now) -> list[str]:
    lines = ["**Today's LeetCode tracking**"]
    for key, member in data["members"].items():
        if not member["active"]:
            continue
        apply_pending_timezone(data, int(key), now)
        day = member_day(member, now)
        count = count_day(member, day)
        lines.append(f"{'✅ Submitted' if count else '⏳ Pending'} <@{key}> · {day} · {count} solves · "
                     f"quest {'✅' if quest_done(member, day) else '—'} · "
                     f"deadline <t:{int(player_deadline(data, int(key), now).timestamp())}:R>")
    return split_lines(lines)


def quest_difficulty_label(quest: dict) -> str:
    difficulty = quest.get("difficulty", "Medium")
    if difficulty == "Easy" and quest.get("constraint"):
        return "Easy + Medium-level constraint"
    return difficulty if difficulty == "Medium" else f"{difficulty} (legacy assignment)"


def quest_constraint_text(quest: dict) -> str:
    constraint = quest.get("constraint")
    if not constraint:
        return ""
    return f"**Required constraint:** {constraint}\nSubmitting this quest attests that you followed the constraint."


def quest_embed(data: dict, guild_id: int, user, now) -> discord.Embed:
    if str(user.id) in data["members"]:
        apply_pending_timezone(data, user.id, now)
    day = player_day(data, user.id, now)
    member = data["members"].get(str(user.id))
    zone = member["timezone"] if member else data["timezone"]
    quest = daily_quest(guild_id, day, data)
    constraint = quest_constraint_text(quest)
    return card("Daily interview quest", f"**[{quest['name']}]({quest['url']})** · {quest_difficulty_label(quest)}\n"
                f"**{day}** · {zone}\nDeadline <t:{int(player_deadline(data, user.id, now).timestamp())}:F>\n"
                f"{'✅ Completed' if member and quest_done(member, day) else 'Not completed yet'}\n\n"
                + (constraint + "\n\n" if constraint else "")
                +
                "Solve it, open LeetCode's **Submissions** tab, open your accepted attempt, and copy its URL. "
                "Press **Submit solve** below or use `/submit` with that URL. "
                "Any logged solve protects your participation streak.", user=user)


def history_pages(member: dict) -> list[str]:
    records = member.get("submissions", [])
    by_day = {}
    for entry in records:
        day = entry.get("date", entry.get("day", "Unknown date"))
        by_day.setdefault(day, []).append(entry)
    lines = [f"**LeetCode history · {member['solved']:,} lifetime solves**"]
    for day in sorted(set(member.get("recent", {})) | set(by_day), reverse=True):
        entries = by_day.get(day, [])
        for entry in entries:
            slug = entry.get("slug")
            name = entry.get("title") or entry.get("name") or (title(slug) if slug else "Unknown problem")
            link = entry.get("url", entry.get("submission_url"))
            problem = f"[{name}]({link})" if link else name
            lines.append(f"**{day}** · {problem} · {entry.get('difficulty', 'Unknown')}")
        bucket = member.get("recent", {}).get(day, {})
        count = sum(bucket.get("counts", [])) + bucket.get("unknown", 0)
        if count > len(entries):
            lines.append(f"**{day}** · {count - len(entries)} additional solves (aggregate-only history)")
    lines.append("Historical links and daily details appear where available; missing details are not treated as zero activity.")
    return split_lines(lines)


def reminders_embed(data: dict, user_id: int, now) -> discord.Embed:
    member = player(data, user_id)
    time = member.get("reminder_time")
    schedule = f"**{time}** local" if time else "Inherited server slots: " + ", ".join(data["reminder_times"])
    late = "on" if member.get("late_reminder", False) else "off"
    if time is None:
        late = "included only when the inherited server schedule has a late slot"
    return card("Your reminder settings", f"Reminders: **{'on' if member['reminders'] else 'off'}**\n"
                f"Daily reminder: {schedule}\nOptional 02:15 reminder: {late}\n"
                f"Timezone: **{member['timezone']}**\n\n"
                "Only pending challenge days receive reminders. Snooze pauses pings for one hour.\n"
                "Use the buttons or `/reminders reminder_time:19:30 late_reminder:false`.")


async def update_personal_reminders(interaction, *, enabled=None, reminder_time=None,
                                    late_reminder=None, toggle_late=False):
    def build(data, now):
        member = player(data, interaction.user.id)
        chosen_late = "02:15" not in reminder_slots(data, member) if toggle_late else late_reminder
        set_reminder_preferences(member, enabled=enabled, reminder_time=reminder_time, late_reminder=chosen_late)
        return reminders_embed(data, interaction.user.id, now)
    await execute(interaction, build, write=True, private=True, view=ReminderSettingsView())


async def snooze_reminders(interaction, minutes=60):
    def build(data, now):
        until = snooze_member(player(data, interaction.user.id), now, minutes=minutes)
        timestamp = int(datetime.fromisoformat(until).timestamp())
        return card("Reminders snoozed", f"Reminder pings pause until <t:{timestamp}:t>. "
                    "Your daily deadline and weekly goal stay the same.")
    await execute(interaction, build, write=True, private=True)


class SubmitModal(discord.ui.Modal, title="Log a LeetCode solve"):
    attempt_url = discord.ui.TextInput(label="Submission URL", max_length=500,
        placeholder="https://leetcode.com/problems/two-sum/submissions/12345/")
    problem_url = discord.ui.TextInput(label="Problem URL (for a /submissions/detail/ link)", required=False,
        max_length=500, placeholder="https://leetcode.com/problems/two-sum/")
    difficulty = discord.ui.TextInput(label="Difficulty (optional, self-reported)", required=False,
        max_length=10, placeholder="Easy, Medium, or Hard")

    async def on_submit(self, interaction: discord.Interaction):
        await save_submission(interaction, self.attempt_url.value.strip(), self.difficulty.value.strip() or None,
                              self.problem_url.value.strip() or None)


class ReminderTimeModal(discord.ui.Modal, title="Choose your reminder time"):
    reminder_time = discord.ui.TextInput(label="Local time (HH:MM)", max_length=5, placeholder="19:30")

    async def on_submit(self, interaction: discord.Interaction):
        await update_personal_reminders(interaction, reminder_time=self.reminder_time.value.strip())


class ReminderSettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_personal_reminders(interaction, enabled=True)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_personal_reminders(interaction, enabled=False)

    @discord.ui.button(label="Set local time", style=discord.ButtonStyle.secondary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReminderTimeModal())

    @discord.ui.button(label="Toggle 02:15 reminder", style=discord.ButtonStyle.secondary)
    async def toggle_late(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_personal_reminders(interaction, toggle_late=True)


class DashboardView(discord.ui.View):
    def __init__(self, submission_url: str | None = None):
        super().__init__(timeout=None)
        if submission_url:
            self.add_item(discord.ui.Button(label="View submission", url=submission_url, row=2))

    @discord.ui.button(label="Submit solve", style=discord.ButtonStyle.success, custom_id="lc:submit", row=0)
    async def open_submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SubmitModal())

    @discord.ui.button(label="My progress", style=discord.ButtonStyle.secondary, custom_id="lc:profile", row=0)
    async def show_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: profile_embed(data, interaction.user, now),
                      private=True, grid_member=interaction.user.id)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, custom_id="lc:leaderboard", row=1)
    async def show_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: leaderboard_embed(data, now), private=True)

    @discord.ui.button(label="Today", style=discord.ButtonStyle.secondary, custom_id="lc:today", row=1)
    async def show_today(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: today_pages(data, now), private=True)

    @discord.ui.button(label="Daily quest", style=discord.ButtonStyle.primary, custom_id="lc:quest", row=0)
    async def show_quest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: quest_embed(data, interaction.guild_id, interaction.user, now),
                      write=True, private=True)

    @discord.ui.button(label="Snooze 1h", style=discord.ButtonStyle.secondary, custom_id="lc:snooze", row=1)
    async def snooze(self, interaction: discord.Interaction, button: discord.ui.Button):
        await snooze_reminders(interaction)

    @discord.ui.button(label="Reminders", style=discord.ButtonStyle.secondary, custom_id="lc:reminders", row=1)
    async def reminder_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: reminders_embed(data, interaction.user.id, now),
                      private=True, view=ReminderSettingsView())


def scheduled_mentions(keys):
    if keys is None:
        return discord.AllowedMentions.none()
    return discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                   users=[discord.Object(id=int(key)) for key in keys])


def report_pages(guild_id: int, day: date, roster: dict, quest: dict | None) -> list[str]:
    done = [(key, state) for key, state in roster.items() if state.get("count", 0) and not state.get("excused")]
    missed = [(key, state) for key, state in roster.items() if not state.get("count", 0) and not state.get("excused")]
    excused = [(key, state) for key, state in roster.items() if state.get("excused")]
    end = max((state["deadline"] for state in roster.values()), default="")
    lines = [f"**LeetCode daily report · {day}**",
             f"Submitted **{len(done)}** · Missed **{len(missed)}** · Excused **{len(excused)}**",
             f"Final deadline: {end}"]
    if quest:
        lines.append(f"Quest: [{quest['name']}]({quest['url']}) · {quest_difficulty_label(quest)}")
        if quest_constraint_text(quest):
            lines.append(quest_constraint_text(quest))
    if day < utcnow().date():
        lines.append("Closed results; delivered after all personal deadlines.")
    for heading, rows in (("✅ Submitted", done), ("❌ Missed", missed), ("Excused", excused)):
        lines.append(f"**{heading}**")
        if not rows:
            lines.append("Nobody.")
        for key, state in rows:
            lines.append(f"<@{key}> · {state.get('count', 0)} solves · quest "
                         f"{'completed' if state.get('quest') else 'incomplete'}")
            for index, link in enumerate(state.get("links", []), 1):
                lines.append(f"↳ [Submission {index}]({link})")
    return [f"{page}\n`lc-report:{guild_id}:{day}:{index}`"
            for index, page in enumerate(split_lines(lines, 1750))]


class TrackerBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.store = None
        self.submit_lock = asyncio.Lock()

    async def setup_hook(self):
        self.store = PostgresStore(os.getenv("DATABASE_URL", ""))
        await self.store.open()
        legacy = Path(os.getenv("LEGACY_DATA_FILE", os.getenv("DATA_FILE", "data.json")))
        guilds, players = await self.store.import_json(legacy)
        if guilds:
            log.info("Imported %s players across %s guilds; legacy source unchanged.", players, guilds)
        self.add_view(DashboardView())
        await self.tree.sync()
        self.post_reports.start()

    async def close(self):
        self.post_reports.cancel()
        if self.store:
            await self.store.close()
        await super().close()

    async def on_ready(self):
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching,
                                                           name="daily LeetCode progress | /submit"))
        log.info("LeetCode tracker online as %s", self.user)

    @tasks.loop(minutes=1)
    async def post_reports(self):
        try:
            for guild_id in await self.store.guild_ids():
                try:
                    await self.process_guild(guild_id)
                except Exception as exc:
                    log_failure(f"Scheduler failed for guild {guild_id}; retry next tick", exc)
        except Exception as exc:
            log_failure("Scheduler scan failed; retry next tick", exc)

    @post_reports.before_loop
    async def before_reports(self):
        await self.wait_until_ready()

    async def process_guild(self, guild_id: int):
        await process_scheduled_guild(self, guild_id, clock=utcnow, mention_factory=scheduled_mentions,
                                     report_builder=report_pages, reminder_view=DashboardView)

    async def find_report_message(self, channel, marker: str):
        """Recover a send whose DB commit failed; Discord has no atomic send+commit."""
        try:
            async for message in channel.history(limit=100):
                if message.author.id == self.user.id and f"`{marker}`" in message.content:
                    return message.id
        except discord.HTTPException as exc:
            log.warning("Cannot reconcile report history (%s); duplicate delivery remains possible.", type(exc).__name__)
        return None


bot = TrackerBot()


async def execute(interaction: discord.Interaction, build, *, write=False, private=False, view=None,
                  grid_member: int | None = None) -> bool:
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this in a Discord server.", ephemeral=True)
        return False
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True, ephemeral=private)
    try:
        if write:
            async with bot.store.edit(interaction.guild_id) as data:
                result = build(data, utcnow())
        else:
            data = await bot.store.guild(interaction.guild_id)
            result = build(data, utcnow())
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return False
    except Exception as exc:
        log_failure("Command storage/build failed", exc)
        await interaction.followup.send("Couldn't load or save that. Check the database connection and try again.", ephemeral=True)
        return False
    kwargs = {"allowed_mentions": discord.AllowedMentions.none(), "ephemeral": private}
    if isinstance(result, list):
        try:
            for page in result:
                await interaction.followup.send(page, **kwargs)
        except discord.HTTPException as exc:
            log_failure("Could not deliver paginated command response", exc)
        return True
    kwargs.update(embed=result, view=view or DashboardView())
    if grid_member is not None:
        try:
            member = player(data, grid_member)
            png = render_activity(member, player_day(data, grid_member, utcnow()))
            kwargs["file"] = discord.File(BytesIO(png), filename="activity.png")
            result.set_image(url="attachment://activity.png")
        except Exception as exc:
            log_failure("Activity image rendering failed; sending text progress", exc)
    try:
        await interaction.followup.send(**kwargs)
    except discord.HTTPException as exc:
        if "file" in kwargs:
            # The solve is already committed. Retry only delivery, never recording.
            kwargs.pop("file")
            result.set_image(url=None)
            try:
                await interaction.followup.send(**kwargs)
            except discord.HTTPException as fallback:
                log_failure("Saved command result but Discord text response failed", fallback)
        else:
            log_failure("Saved command result but Discord response failed", exc)
    return True


@bot.tree.command(name="setup", description="Set the report channel and default timezone for new members")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, timezone_name: str = "America/Vancouver"):
    def build(data, now):
        zone = timezone(timezone_name).key
        data["timezone"], data["report_channel_id"] = zone, channel.id
        sync_roster(data, now, guild_id=interaction.guild_id)
        return card("Tracker configured", f"Reports and reminders → {channel.mention}\nDefault timezone → **{zone}**\n"
                    "Each member's day closes at **3:15 AM locally**. New members get a **20:00 local** reminder; "
                    "the late 02:15 reminder is optional.\n"
                    "The combined daily report waits for the final participant deadline.\n"
                    "Existing members keep their timezones. Personal opt-out: `/reminders enabled:false`.", GREEN)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="join", description="Join with PST/MST/CST/EST/UTC or an IANA timezone")
@app_commands.guild_only()
async def join(interaction: discord.Interaction, timezone_name: str = ""):
    def build(data, now):
        sync_roster(data, now, guild_id=interaction.guild_id)
        current = data["members"].get(str(interaction.user.id))
        zone = timezone(timezone_name or (current or {}).get("timezone", data["timezone"])).key
        day = challenge_day(now, zone)
        added = enroll(data, interaction.user.id, day, zone)
        sync_roster(data, now, guild_id=interaction.guild_id)
        day = player_day(data, interaction.user.id, now)
        member = player(data, interaction.user.id)
        try:
            assigned = daily_quest(interaction.guild_id, day, data)
            quest_text = (f"\n\n**Today's quest:** [{assigned['name']}]({assigned['url']}) · "
                          f"{quest_difficulty_label(assigned)}\n")
            if assigned.get("constraint"):
                quest_text += quest_constraint_text(assigned) + "\n"
        except ValueError:
            quest_text = "\n\nToday's quest is unavailable; you can still log other LeetCode solves.\n"
        return card("Tracking joined" if added else "You're already tracking", f"Timezone: **{zone}**\n"
                    f"Deadline <t:{int(player_deadline(data, interaction.user.id, now).timestamp())}:F>.\n"
                    f"Weekly goal: **{member.get('weekly_goal', 4)} days/week** · change it with `/goal days:4`.\n"
                    f"Reminders: **{'on' if member['reminders'] else 'off'}**. "
                    "Choose a time with `/reminders` or opt out with `/reminders enabled:false`."
                    + quest_text +
                    "After solving, open LeetCode's **Submissions** tab, open your accepted attempt, and copy its URL. "
                    "Press **Submit solve** below or use `/submit`. For a `/submissions/detail/` link, "
                    "also include the problem URL.\nPST/MST/CST/EST aliases follow daylight saving.",
                    GREEN, interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="leave", description="Pause tracking and keep all your history and lifetime totals")
@app_commands.guild_only()
async def leave(interaction: discord.Interaction):
    def build(data, now):
        player(data, interaction.user.id)
        leave_member(data, interaction.user.id, now)
        return card("Tracking paused", "Your history, totals, timezone, and reminder preference are preserved. "
                    "Rejoining starts a fresh streak/tracking period.", MUTED, interaction.user)
    await execute(interaction, build, write=True, private=True)


async def save_submission(interaction: discord.Interaction, url: str, difficulty: str | None = None,
                          problem_url: str | None = None):
    try:
        if difficulty:
            difficulty = difficulty.strip().title()
            if difficulty not in {"Easy", "Medium", "Hard"}:
                raise ValueError("Difficulty is optional; choose Easy, Medium, or Hard, or leave it blank.")
        attempt = submission_from_url(url, problem_url)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    async with bot.submit_lock:
        def build(data, now):
            sync_roster(data, now, guild_id=interaction.guild_id)
            member = data["members"].get(str(interaction.user.id))
            zone = member["timezone"] if member else data["timezone"]
            day = player_day(data, interaction.user.id, now)
            enroll(data, interaction.user.id, day, zone)
            earned = record(data, interaction.user.id, day, attempt["slug"], guild_id=interaction.guild_id,
                            difficulty=difficulty, now=now, submission_url=attempt["url"],
                            submission_id=attempt["submission_id"])
            sync_roster(data, now, guild_id=interaction.guild_id)
            embed = card("Welcome back! Solve recorded" if earned.get("comeback") else "Solve recorded",
                         f"**[{title(attempt['slug'])}]({attempt['problem_url']})** · "
                         f"{earned['difficulty']}{' (self-reported)' if difficulty else ''}\n"
                         f"[**View submission**]({attempt['url']})\n"
                         f"**{day}** · {zone} · deadline <t:{int(player_deadline(data, interaction.user.id, now).timestamp())}:R>\n"
                         f"{'✅ Daily quest completed' if earned['quest'] else 'Daily solve logged'}",
                         GREEN, interaction.user)
            if earned.get("comeback"):
                embed.add_field(name="Keep building", value="Today's solve starts your next streak. "
                                "Your previous solves and best streak still count toward your progress.", inline=False)
            if earned["quest"]:
                assigned = data.get("quests", {}).get(day.isoformat(), {})
                embed.add_field(name="Daily quest · self-attested", value=quest_difficulty_label(assigned)
                                + ("\n" + quest_constraint_text(assigned) if assigned.get("constraint") else ""),
                                inline=False)
            add_progress(embed, data, interaction.user.id, now)
            return embed
        await execute(interaction, build, write=True, view=DashboardView(attempt["url"]),
                      grid_member=interaction.user.id)


@bot.tree.command(name="submit", description="Log a trusted LeetCode attempt and show your daily activity and streak")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 5, key=lambda interaction: (interaction.guild_id, interaction.user.id))
async def submit(interaction: discord.Interaction, url: str, difficulty: Difficulty | None = None,
                 problem_url: str | None = None):
    await save_submission(interaction, url, difficulty, problem_url)


@bot.tree.command(name="status", description="Show tracking totals, daily activity, streaks, timezone, and reminders")
@app_commands.guild_only()
async def status(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    await execute(interaction, lambda data, now: profile_embed(data, target, now), grid_member=target.id)


@bot.tree.command(name="progress", description="Show your weekly or monthly progress, daily activity grid, and streak")
@app_commands.guild_only()
async def progress_command(interaction: discord.Interaction, period: Period = "week"):
    def build(data, now):
        embed = card(f"This {period}'s LeetCode progress", user=interaction.user)
        add_progress(embed, data, interaction.user.id, now, periods=(period,))
        return embed
    await execute(interaction, build, grid_member=interaction.user.id)


@bot.tree.command(name="today", description="Show who's submitted and who's pending with personal deadlines")
@app_commands.guild_only()
async def today(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: today_pages(data, now))


@bot.tree.command(name="leaderboard", description="Rank active members by streak, missed days, and lifetime solves")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction, page: app_commands.Range[int, 1, 100000] = 1):
    await execute(interaction, lambda data, now: leaderboard_embed(data, now, page))


@bot.tree.command(name="history", description="Browse preserved submissions and aggregate-only historical counts")
@app_commands.guild_only()
async def history(interaction: discord.Interaction, member: discord.Member | None = None,
                  page: app_commands.Range[int, 1, 100000] = 1):
    target = member or interaction.user
    def build(data, now):
        pages = history_pages(player(data, target.id))
        if page > len(pages):
            raise ValueError(f"Choose a page from 1 to {len(pages)}.")
        return [pages[page - 1] + f"\nPage {page}/{len(pages)} · `/history page:{page + 1}` for the next page."
                if page < len(pages) else pages[page - 1] + f"\nPage {page}/{len(pages)}."]
    await execute(interaction, build)


@bot.tree.command(name="quest", description="Show today's Medium or constrained Easy interview quest")
@app_commands.guild_only()
async def quest(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: quest_embed(data, interaction.guild_id, interaction.user, now), write=True)


@bot.tree.command(name="reminders", description="Enable or opt out of personal pending-submission reminder pings")
@app_commands.guild_only()
async def reminders(interaction: discord.Interaction, enabled: bool | None = None,
                    reminder_time: str | None = None, late_reminder: bool | None = None):
    await update_personal_reminders(interaction, enabled=enabled, reminder_time=reminder_time,
                                    late_reminder=late_reminder)


@bot.tree.command(name="goal", description="Choose a weekly participation goal from three to seven days")
@app_commands.guild_only()
async def goal(interaction: discord.Interaction, days: app_commands.Range[int, 3, 7] = 4):
    def build(data, now):
        member = player(data, interaction.user.id)
        set_weekly_goal(member, days)
        return card("Weekly goal saved", f"Aim for **{days} days/week**. Any logged solve on a challenge day counts.\n"
                    + progress_text(member, player_day(data, interaction.user.id, now), "week", data),
                    user=interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="snooze", description="Pause personal reminder pings without changing your deadline")
@app_commands.guild_only()
async def snooze(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 1440] = 60):
    await snooze_reminders(interaction, minutes)


@bot.tree.command(name="timezone", description="Schedule a timezone change at your next deadline without losing your streak")
@app_commands.guild_only()
async def timezone_command(interaction: discord.Interaction, timezone_name: str):
    def build(data, now):
        player(data, interaction.user.id)
        pending = change_timezone(data, interaction.user.id, timezone_name, now)
        timestamp = int(datetime.fromisoformat(pending["effective_at"]).timestamp())
        return card("Timezone change scheduled", f"**{pending['timezone']}** takes effect <t:{timestamp}:F>.\n"
                    "Your current day keeps its existing deadline. Your streak and previous activity stay with you.",
                    user=interaction.user)
    await execute(interaction, build, write=True, private=True)


def buddy_status_pages(data: dict, user_id: int, now) -> list[str]:
    player(data, user_id)
    lines = ["**Study buddies**", "Invite with `/buddy action:invite member:@friend`; "
             "accept with `/buddy action:accept member:@friend`. Either person can remove the connection anytime."]
    rows = buddy_progress(data, user_id, now)
    if not rows:
        lines.append("No accepted connections yet.")
    for row in rows:
        lines.append(f"<@{row['user_id']}> · {row['today_count']} solves today · {row['streak']} day streak · "
                     f"{row['week'].get('goal_days', row['week']['days'])}/{row['week'].get('goal', 4)} days this week")
    for pair in data.get("buddies", {}).values():
        if pair.get("status") != "pending":
            continue
        if pair["invitee"] == str(user_id):
            lines.append(f"Invitation from <@{pair['inviter']}> · `/buddy action:accept member:@friend` to accept.")
        elif pair["inviter"] == str(user_id):
            lines.append(f"Waiting for <@{pair['invitee']}> to accept your invitation.")
    return split_lines(lines)


@bot.tree.command(name="buddy", description="Invite, accept, remove, or check a study buddy by mutual consent")
@app_commands.guild_only()
async def buddy(interaction: discord.Interaction, action: BuddyAction = "status", member: discord.Member | None = None):
    def build(data, now):
        player(data, interaction.user.id)
        if action == "status":
            return buddy_status_pages(data, interaction.user.id, now)
        if member is None:
            raise ValueError("Choose the member you want to invite, accept, or remove.")
        if getattr(member, "bot", False):
            raise ValueError("Choose a person who has joined the tracker.")
        if action == "invite":
            invite_buddy(data, interaction.user.id, member.id)
            return card("Buddy invitation ready", f"Ask <@{member.id}> to run `/buddy action:accept` "
                        "and select you in the member field. Their acceptance enables shared progress.")
        if action == "accept":
            accept_buddy(data, interaction.user.id, member.id)
            return card("Study buddies connected", f"You and <@{member.id}> can now see each other's progress "
                        "in your buddy summaries. `/buddy action:remove` ends the connection anytime.")
        if action == "remove":
            remove_buddy(data, interaction.user.id, member.id)
            return card("Buddy connection removed", f"Your connection with <@{member.id}> has been removed.")
        raise ValueError("Choose invite, accept, remove, or status.")
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="reminder_settings", description="Admin: set local reminder hours or disable server reminders")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def reminder_settings(interaction: discord.Interaction, enabled: bool = True,
                            first_hour: app_commands.Range[int, 0, 23] = 20,
                            final_hour: app_commands.Range[int, 0, 23] = 2):
    def build(data, now):
        if first_hour == 3 or final_hour == 3:
            raise ValueError("Avoid the 3 AM hour because it straddles the 3:15 cutoff. Use 2 for the final nudge.")
        slots = [f"{first_hour:02}:00", "02:15" if final_hour == 2 else f"{final_hour:02}:00"]
        data["reminders_enabled"] = enabled
        data["reminder_times"] = sorted(set(slots), key=lambda slot: (int(slot[:2]) * 60 + int(slot[3:]) - 195) % 1440)
        return card("Reminder schedule saved", f"{', '.join(data['reminder_times'])} in each member's timezone. "
                    "Personal opt-outs remain in effect." if enabled else "Server reminders disabled.", GREEN)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="storage", description="Admin: inspect PostgreSQL storage usage and player count")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def storage(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        usage = await bot.store.usage()
        embed = card("PostgreSQL storage", f"Entire database: **{usage['database_bytes'] / 1024**2:.2f} MiB**\n"
                     f"Bot tables and indexes: **{usage['bot_bytes'] / 1024**2:.2f} MiB**\n"
                     f"Players: **{usage['players']}**\nHistory is retained; provision capacity as it grows.")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        log_failure("Storage check failed", exc)
        await interaction.followup.send("Could not query storage usage. Check the database connection.", ephemeral=True)


@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Manage Server permission for that command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"Try again in {error.retry_after:.0f} seconds."
    else:
        command = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
        original = log_failure(f"Slash command /{command} failed", error)
        message = f"Something went wrong ({type(original).__name__}). The error is in the bot logs."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("DISCORD_TOKEN")
    if not token or not os.getenv("DATABASE_URL"):
        raise SystemExit("Set DISCORD_TOKEN and DATABASE_URL in .env beside bot.py.")
    try:
        bot.run(token)
    except Exception as exc:
        log.error("Startup failed (%s). Check credentials, DATABASE_URL, network access, and database permissions.",
                  type(exc).__name__)
        raise SystemExit(1)
