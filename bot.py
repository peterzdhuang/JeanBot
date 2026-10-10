"""Discord LeetCode Arena: personal deadlines, PostgreSQL counters, and tiny RPGs."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from catalog import title
from game import (
    ITEMS, PERKS, SHOPKEEPER, SHOP_LINES, buy, choose_perk, drink,
    equip as equip_item, feed as feed_pet, pet_status, rename_pet, retreat, start_run,
)
from storage import PostgresStore
from tracker import (
    DIFFICULTIES, LEVEL_XP, REWARD_LIMIT, STREAK_MILESTONES, challenge_day,
    daily_quest, deadline, enroll, problem_from_url, prune_recent, rankings, record, reminder_due,
    stats, timezone, utcnow,
)

load_dotenv(Path(__file__).resolve().with_name(".env"))
log = logging.getLogger("arena")
PURPLE, GREEN, GOLD, PINK, MUTED = 0x8B5CF6, 0x2DD4BF, 0xFBBF24, 0xF472B6, 0x64748B
Difficulty = Literal["Easy", "Medium", "Hard"]
HYPE = (
    "Brain gains secured. The compiler fears you.",
    "Another problem down. Keep cooking. 🍳",
    "Your neurons have officially clocked in.",
    "The grind has receipts. Jean-Luc absolutely does not.",
)
REMINDER_JOKES = (
    "Your streak called. It would prefer not to become a historical artifact.",
    "The LeetCode tab you opened three hours ago would like a word.",
    "Tomorrow-you filed a formal request: one problem, please.",
    "A tiny solve now beats a dramatic comeback speech tomorrow.",
)
LAST_CALL_JOKES = (
    "Your streak has main-character energy. Please do not write a tragic finale.",
    "3:15 AM is approaching. Your streak is sweating in O(n).",
    "Last call for brain gains. The goblin has put on his deadline hat.",
)


def log_failure(context: str, error: Exception) -> Exception:
    """Expose the original cause/traceback without printing configured credentials."""
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


def progress(value: int, total: int, width: int = 10) -> str:
    filled = min(width, max(0, value * width // max(1, total)))
    return "▰" * filled + "▱" * (width - filled)


def safe(text: str) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(text))


def fit_lines(lines: list[str], limit: int = 1000, empty: str = "Nobody yet.") -> str:
    result, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit - 35:
            result.append("… more in /leaderboard")
            break
        result.append(line)
        used += len(line) + 1
    return "\n".join(result) or empty


def card(name: str, description: str = "", color: int = PURPLE, user=None) -> discord.Embed:
    embed = discord.Embed(title=name, description=description, color=color, timestamp=utcnow())
    embed.set_footer(text="LEETCODE ARENA • one problem. every day. • Jeancoins ≠ real money")
    if user is not None:
        embed.set_author(name=user.display_name[:256], icon_url=str(user.display_avatar.url))
        embed.set_thumbnail(url=str(user.display_avatar.url))
    return embed


def player(data: dict, user_id: int) -> dict:
    member = data["members"].get(str(user_id))
    if not member:
        raise ValueError("Join first: /join timezone_name:PST")
    return member


def player_day(data: dict, user_id: int, now):
    zone = data["members"].get(str(user_id), {}).get("timezone", data["timezone"])
    return challenge_day(now, zone)


def profile_embed(data: dict, target, now) -> discord.Embed:
    day = player_day(data, target.id, now)
    result = stats(data, target.id, day)
    if result is None:
        return card("🌱 Your run starts here", "Use /join with your timezone, then /submit.", user=target)
    member = player(data, target.id)
    equipped = member["equipped"]
    aura = ITEMS.get(equipped.get("aura"), {})
    custom_title = ITEMS.get(equipped.get("title"), {}).get("name", result["title"])
    board = rankings(data, now)
    rank = next((index for index, row in enumerate(board, 1) if row[0] == str(target.id)), None)
    embed = card(f"{custom_title} · Level {result['level']}",
                 f"{'🟢 In the arena' if result['active'] else '⚪ Taking a break'}\n"
                 f"{'✅' if result['today'] else '⏳'} **{result['today_count']}** solves this challenge day\n"
                 f"Cutoff: <t:{int(deadline(day, member['timezone']).timestamp())}:R> · 3:15 AM local",
                 aura.get("color", GOLD if result["streak"] >= 7 else PURPLE), target)
    embed.add_field(name="🔥 Current / best streak", value=f"**{result['streak']} / {result['longest']}** days")
    embed.add_field(name="🏆 Server rank", value=f"**#{rank}** / {len(board)}" if rank else "Unranked")
    embed.add_field(name="🪙 Jeancoins", value=f"**{result['wallet']:,}**")
    embed.add_field(name="🧩 Lifetime solve counts", value=f"**{result['solved']:,}** total\n"
                    + " · ".join(f"{name}: {count}" for name, count in zip(DIFFICULTIES, result["counts"])), inline=False)
    embed.add_field(name="🎯 This tracking period", value=f"{result['completed_days']} days cleared · {result['missed']} missed")
    embed.add_field(name="🎲 Quests / dungeon wins", value=f"{result['quests']} / {member['dungeon_wins']}")
    embed.add_field(name=f"⚡ {result['xp']:,} XP", value=f"{progress(result['level_xp'], LEVEL_XP)} "
                    f"{result['level_xp']}/{LEVEL_XP} to level {result['level'] + 1}", inline=False)
    recent = [day - timedelta(days=offset) for offset in range(6, -1, -1)]
    tiles = ["⬛" if item < result["joined"] else "🟩" if item in result["days"] else
             "🟨" if item == day else "🟥" for item in recent]
    embed.add_field(name="📅 Last seven challenge days", value=" ".join(tiles) + "\n"
                    + " · ".join(item.strftime("%a") for item in recent), inline=False)
    pet = pet_status(member, now)
    embed.add_field(name="🐾 Companion", value=f"{pet['emoji']} **{safe(pet['name'])}** · "
                    f"Lv.{pet['level']} {pet['stage']} · fullness {pet['fullness']}%" if pet else
                    "An egg is waiting in /shop.")
    relic = ITEMS.get(equipped.get("relic"))
    embed.add_field(name="🔮 Equipped relic", value=relic["name"] if relic else "No relic yet.")
    embed.add_field(name="🎖️ Badge cabinet", value="\n".join(result["badges"]) or "Your first solve unlocks a badge.", inline=False)
    embed.set_footer(text=f"{member['timezone']} • tracking since {result['joined']} • reminders "
                     f"{'on' if member['reminders'] else 'muted'} • totals only, no problem history")
    return embed


def leaderboard_embed(data: dict, now) -> discord.Embed:
    rows = rankings(data, now)
    lines = []
    for index, (key, result) in enumerate(rows[:15], 1):
        medal = ("🥇", "🥈", "🥉")[index - 1] if index <= 3 else f"**#{index}**"
        lines.append(f"{medal} <@{key}>\n↳ 🔥 **{result['streak']}d** · ⚡ {result['xp']:,} XP · "
                     f"🪙 {result['wallet']:,} · 💤 {result['missed']} missed")
    embed = card("🏆 The Arena", fit_lines(lines, 3800, "The podium is empty. /join to claim it."), GOLD)
    embed.add_field(name="Ranking", value="Current streak → fewer missed days → XP. Each player uses their own timezone.", inline=False)
    return embed


def today_embed(data: dict, now) -> discord.Embed:
    done, pending = [], []
    for key, member in data["members"].items():
        if not member["active"]:
            continue
        day = challenge_day(now, member["timezone"])
        count = sum(member["recent"].get(day.isoformat(), {}).get("counts", []))
        if count:
            done.append(f"<@{key}> · {count} solves · {day:%b %d}")
        else:
            pending.append(f"<@{key}> · cutoff <t:{int(deadline(day, member['timezone']).timestamp())}:R>")
    total = len(done) + len(pending)
    embed = card("⚡ Today's arena", f"{progress(len(done), total)} **{len(done)}/{total}** players done",
                 GREEN if done and not pending else PURPLE)
    embed.add_field(name="✅ Cleared", value=fit_lines(done), inline=False)
    embed.add_field(name="⏳ Still cooking", value=fit_lines(pending), inline=False)
    embed.set_footer(text="Personal challenge days run 3:15 AM → 3:15 AM • no midnight resets")
    return embed


def quest_embed(data: dict, guild_id: int, user, now, difficulty: str | None = None) -> discord.Embed:
    day = player_day(data, user.id, now)
    member = data["members"].get(str(user.id))
    zone = member["timezone"] if member else data["timezone"]
    bits = member["recent"].get(day.isoformat(), {}).get("quests", 0) if member else 0
    embed = card("🎲 Daily quest board", "One pick per difficulty. Any solve protects your streak; these picks add bonus loot.", PINK, user)
    for index, name in enumerate(DIFFICULTIES):
        if difficulty and difficulty != name:
            continue
        quest = daily_quest(guild_id, day, name)
        embed.add_field(name=f"{'✅' if bits & (1 << index) else '🟣'} {name} quest",
                        value=f"**[{quest['name']}]({quest['url']})**\n"
                        f"Bonus: **+{quest['coins']} Jeancoins · +{quest['xp']} XP**\n"
                        f"/submit url:{quest['url']}", inline=False)
    embed.set_footer(text=f"25 Easy / 75 Medium / 25 Hard in rotation • refresh at 3:15 AM • {zone}")
    return embed


def shop_embed(member: dict) -> discord.Embed:
    embed = card(SHOPKEEPER, random.choice(SHOP_LINES) + f"\n\nYour wallet: **{member['wallet']:,} Jeancoins**", GOLD)
    for key, item in ITEMS.items():
        embed.add_field(name=f"{item['name']} · {item['price']} JC",
                        value=f"{item['description']}\n/buy item:{key}", inline=False)
    embed.set_footer(text="Cosmetics: /equip • one pet • one equipped relic • biscuits: /feed • juice: /dungeon heal")
    return embed


def pet_embed(member: dict, user, now) -> discord.Embed:
    pet = pet_status(member, now)
    if not pet:
        return card("🥚 A friend-shaped investment", "Buy a Suspicious Egg for 80 Jeancoins: /buy item:egg", user=user)
    embed = card(f"{pet['emoji']} {pet['name']}", f"**Lv.{pet['level']} {pet['stage']}**\n{pet['mood']}", GREEN, user)
    embed.add_field(name="🍪 Fullness", value=f"{progress(pet['fullness'], 100)} {pet['fullness']}%")
    embed.add_field(name="⚡ Pet XP", value=f"{pet['xp']} total · {pet['xp'] % 30}/30 to next level")
    embed.add_field(name="Care guide", value="Loses 2 fullness/hour; never dies. /feed uses a Byte Biscuit (+50 fullness, +10 pet XP).\n"
                    "At 20+ fullness, your first 10 daily solves grant pet XP and +2 Jeancoins.\n"
                    "Evolves into Gremlin at level 3, Overlord at level 7. /pet name:... renames it.", inline=False)
    return embed


def dungeon_embed(member: dict, user) -> discord.Embed:
    run = member["run"]
    if not run:
        return card("🕳️ The Bug Below", "Start /dungeon start, then submit solves to fight through 10 floors.\n"
                    "One new run per challenge day. Perk drafts at floors 3/6/9; bosses at 5/10.\n"
                    "Retreat banks all loot. Falling banks half. Your regular solve rewards stay safe.", PINK, user)
    embed = card(f"🕳️ The Bug Below · {run['status'].title()}", f"**{run['role']}** · floor **{run['floor']}/10**\n"
                 f"❤️ {progress(run['hp'], run['max_hp'])} {run['hp']}/{run['max_hp']} HP\n"
                 f"🪙 **{run['loot']}** unbanked Jeancoins · best floor **{member['best_floor']}**", PINK, user)
    embed.add_field(name="Your build", value="\n".join(PERKS[key][0] for key in run["perks"]) or "No perks drafted yet.")
    if run["choices"]:
        embed.add_field(name="🎲 Choose ONE before the next room", value="\n".join(
            f"**{PERKS[key][0]}** — {PERKS[key][1]}\n/dungeon choose perk:{key}" for key in run["choices"]), inline=False)
    embed.add_field(name="Your move", value="/submit advances one room (first 10 daily solves).\n"
                    "/dungeon heal uses Debug Juice. /dungeon retreat banks loot.", inline=False)
    return embed


class DashboardView(discord.ui.View):
    def __init__(self, problem_url: str | None = None):
        super().__init__(timeout=None)
        if problem_url:
            self.add_item(discord.ui.Button(label="Open problem", emoji="🧩", url=problem_url, row=1))

    @discord.ui.button(label="My profile", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id="lc:profile")
    async def show_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: profile_embed(data, interaction.user, now), private=True)

    @discord.ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="lc:leaderboard")
    async def show_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: leaderboard_embed(data, now), private=True)

    @discord.ui.button(label="Today", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="lc:today")
    async def show_today(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: today_embed(data, now), private=True)

    @discord.ui.button(label="Daily quests", emoji="🎲", style=discord.ButtonStyle.primary, custom_id="lc:quest")
    async def show_quest(self, interaction: discord.Interaction, button: discord.ui.Button):
        await execute(interaction, lambda data, now: quest_embed(data, interaction.guild_id, interaction.user, now), private=True)


class TrackerBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.store = None
        self.submit_lock = asyncio.Lock()
        # Volatile, bounded dedup only. No identifiers are written to PostgreSQL.
        self.seen = {}

    async def setup_hook(self):
        self.store = PostgresStore(os.getenv("DATABASE_URL", ""))
        await self.store.open()
        legacy = Path(os.getenv("LEGACY_DATA_FILE", os.getenv("DATA_FILE", "data.json")))
        guilds, players = await self.store.import_json(legacy)
        if guilds:
            log.info("Imported %s player totals across %s guilds; old JSON unchanged.", players, guilds)
        self.add_view(DashboardView())
        await self.tree.sync()
        self.post_reports.start()
        self.rotate_presence.start()

    async def close(self):
        self.post_reports.cancel()
        self.rotate_presence.cancel()
        if self.store:
            await self.store.close()
        await super().close()

    async def on_ready(self):
        log.info("LeetCode Arena online as %s", self.user)

    @tasks.loop(minutes=10)
    async def rotate_presence(self):
        text = random.choice(("3:15 AM is the final boss | /submit", "Jean-Luc's suspicious shop | /shop",
                              "feed your streak AND your pet | /quest", "the Bug Below is waiting | /dungeon start"))
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))

    @rotate_presence.before_loop
    async def before_presence(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def post_reports(self):
        try:
            for guild_id in await self.store.guild_ids():
                try:
                    await self.process_guild(guild_id)
                except Exception as exc:
                    log.error("Report/reminder failed for guild %s (%s); will retry.", guild_id, type(exc).__name__)
        except Exception as exc:
            log.error("Report scan failed (%s); will retry.", type(exc).__name__)

    @post_reports.before_loop
    async def before_reports(self):
        await self.wait_until_ready()

    async def process_guild(self, guild_id: int):
        snapshot = await self.store.guild(guild_id)
        if not snapshot["report_channel_id"]:
            return
        now = utcnow()
        groups = {}
        zones = set()
        for key, member in snapshot["members"].items():
            if not member["active"]:
                continue
            zones.add(member["timezone"])
            slot = reminder_due(snapshot, member, now)
            if slot:
                groups.setdefault((member["timezone"], slot), []).append(key)
        channel_id = snapshot["report_channel_id"]
        channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        # Commit each delivered batch; a later HTTP failure won't resend earlier batches.
        for (zone, slot), keys in groups.items():
            for offset in range(0, len(keys), 50):
                async with self.store.edit(guild_id) as data:
                    current = utcnow()
                    batch = [key for key in keys[offset:offset + 50]
                             if data["members"][key]["timezone"] == zone
                             and reminder_due(data, data["members"][key], current) == slot]
                    if not batch:
                        continue
                    day = challenge_day(current, zone)
                    last_call = slot == data["reminder_times"][-1]
                    embed = card("⏰ Last nudge, legends" if last_call else "🧠 Brain gains appointment",
                                 random.choice(LAST_CALL_JOKES if last_call else REMINDER_JOKES)
                                 + f"\n\n**One solve. One /submit.** Deadline <t:{int(deadline(day, zone).timestamp())}:R> "
                                 f"(3:15 AM, {zone}).", GOLD if last_call else PURPLE)
                    quest = daily_quest(guild_id, day)
                    embed.add_field(name="Need a starting point?", value=f"[{quest['name']}]({quest['url']}) · "
                                    f"+{quest['coins']} quest Jeancoins")
                    await channel.send(content=" ".join(f"<@{key}>" for key in batch), embed=embed, view=DashboardView(),
                                       allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                           replied_user=False, users=[discord.Object(id=int(key)) for key in batch]))
                    for key in batch:
                        data["members"][key]["reminder_marks"][slot] = day.isoformat()
        for zone in zones:
            async with self.store.edit(guild_id) as data:
                current = utcnow()
                day = challenge_day(current, zone) - timedelta(days=1)
                eligible = [(key, member) for key, member in data["members"].items()
                            if member["active"] and member["timezone"] == zone and member["joined"] <= day.isoformat()
                            and member["reported_day"] < day.isoformat()]
                if not eligible:
                    continue
                done, missed = [], []
                for key, member in eligible:
                    count = sum(member["recent"].get(day.isoformat(), {}).get("counts", []))
                    (done if count else missed).append(f"<@{key}> · {count} solves")
                embed = card("🌙 Local arena wrap-up", f"**{day:%A, %B %d}** · {zone}\n"
                             f"**{len(done)}/{len(eligible)}** players cleared the day.", GREEN if not missed else PURPLE)
                embed.add_field(name="✅ Cleared", value=fit_lines(done), inline=False)
                embed.add_field(name="💤 Missed", value=fit_lines(missed), inline=False)
                embed.add_field(name="Fresh start", value="A missed day is a plot point, not the whole movie. New /quest picks are live.", inline=False)
                await channel.send(embed=embed, view=DashboardView(), allowed_mentions=discord.AllowedMentions.none())
                for _, member in eligible:
                    member["reported_day"] = day.isoformat()
                    prune_recent(member, challenge_day(current, zone))


bot = TrackerBot()


async def execute(interaction: discord.Interaction, build, *, write=False, private=False, view=None) -> bool:
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this in a Discord server.", ephemeral=True)
        return False
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True, ephemeral=private)
    try:
        if write:
            async with bot.store.edit(interaction.guild_id) as data:
                embed = build(data, utcnow())
        else:
            data = await bot.store.guild(interaction.guild_id)
            embed = build(data, utcnow())
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return False
    except Exception as exc:
        log_failure("Command storage/build failed", exc)
        await interaction.followup.send("Couldn't load or save that. Check the database connection and try again.", ephemeral=True)
        return False
    try:
        await interaction.followup.send(embed=embed, view=view or DashboardView(),
                                        allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        # The database commit already succeeded; do not encourage a second submit.
        log.error("Saved command result but Discord response failed (%s).", type(exc).__name__)
    return True


@bot.tree.command(name="setup", description="Set the report channel and default timezone for new players")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel, timezone_name: str = "America/Vancouver"):
    def build(data, now):
        zone = timezone(timezone_name).key
        data["timezone"] = zone
        data["report_channel_id"] = channel.id
        return card("🛠️ Arena configured", f"Reports and nudges → {channel.mention}\nDefault timezone → **{zone}**\n"
                    "Everyone joins with /join timezone_name:PST (or MST/CST/EST/IANA).\n"
                    "Cutoff: **3:15 AM per player**. Default nudges: **8 PM and 2:15 AM locally**.\n"
                    "Existing players keep their own timezones.", GREEN)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="join", description="Join with your timezone: PST, MST, CST, EST, UTC, or an IANA region")
@app_commands.guild_only()
async def join(interaction: discord.Interaction, timezone_name: str = ""):
    def build(data, now):
        current = data["members"].get(str(interaction.user.id))
        zone = timezone(timezone_name or (current or {}).get("timezone", data["timezone"])).key
        day = challenge_day(now, zone)
        added = enroll(data, interaction.user.id, day, zone)
        return card("⚔️ Welcome to the Arena" if added else "You're already in",
                    f"Timezone: **{zone}**\nToday closes <t:{int(deadline(day, zone).timestamp())}:R> at **3:15 AM locally**.\n"
                    "Protect your streak with /submit. /quest → /shop → /pet → /dungeon start.\n"
                    "PST/MST/CST/EST follow daylight saving; use America/Phoenix for Arizona.", GREEN, interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="leave", description="Pause tracking; keep your wallet, pet, items, and lifetime totals")
@app_commands.guild_only()
async def leave(interaction: discord.Interaction):
    def build(data, now):
        member = player(data, interaction.user.id)
        member["active"] = False
        member["left_day"] = challenge_day(now, member["timezone"]).isoformat()
        return card("👋 Taking a breather", "Tracking paused. Your totals and gear stay.\nRejoining starts a fresh streak/tracking period.", MUTED, interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="submit", description="Submit a trusted LeetCode link and earn XP, Jeancoins, and dungeon progress")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 15, key=lambda interaction: (interaction.guild_id, interaction.user.id))
async def submit(interaction: discord.Interaction, url: str, difficulty: Difficulty | None = None):
    try:
        slug, canonical = problem_from_url(url)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    # Acknowledge before queueing for the lock: Discord requires a prompt response.
    await interaction.response.defer(thinking=True)
    async with bot.submit_lock:
        cache_key = (interaction.guild_id, interaction.user.id)
        fingerprint = hashlib.sha256(slug.encode()).digest()
        cache_day = None

        def build(data, now):
            nonlocal cache_day
            member = data["members"].get(str(interaction.user.id))
            zone = member["timezone"] if member else data["timezone"]
            day = challenge_day(now, zone)
            cache_day = day.isoformat()
            cached = bot.seen.get(cache_key)
            if cached and cached[0] == cache_day and fingerprint in cached[1]:
                raise ValueError("Already counted that problem today (while this bot session is running). Try another.")
            enroll(data, interaction.user.id, day, zone)
            before = stats(data, interaction.user.id, day)
            earned = record(data, interaction.user.id, day, slug, guild_id=interaction.guild_id,
                            difficulty=difficulty, now=now)
            result = stats(data, interaction.user.id, day)
            milestone = earned["first"] and result["streak"] in STREAK_MILESTONES
            embed = card("🔥 STREAK MILESTONE!" if milestone else "⚔️ Solve secured",
                         f"**[{title(slug)}]({canonical})** · {earned['difficulty']}\n{random.choice(HYPE)}",
                         GOLD if milestone else GREEN, interaction.user)
            embed.add_field(name="🪙 Jeancoins", value=f"**+{earned['coins']}** · wallet {result['wallet']:,}")
            embed.add_field(name="⚡ XP", value=f"**+{earned['xp']}** · level {result['level']}")
            embed.add_field(name="🔥 Streak", value=f"**{result['streak']}** days · best {result['longest']}")
            if earned["quest"]:
                embed.add_field(name="🎲 Quest cleared", value="Difficulty quest bonus included. One claim per difficulty/day.", inline=False)
            if result["level"] > before["level"]:
                embed.add_field(name="🚀 Level up", value=f"Level {before['level']} → **{result['level']}**", inline=False)
            new_badges = [badge for badge in result["badges"] if badge not in before["badges"]]
            if new_badges:
                embed.add_field(name="🎖️ New badges", value="\n".join(new_badges), inline=False)
            if earned["dungeon"]:
                embed.add_field(name="🕳️ Dungeon encounter", value=earned["dungeon"]["text"], inline=False)
            if not earned["rewarded"]:
                embed.add_field(name="Daily reward limit", value=f"Counted! Normal rewards and dungeon/pet progress stop after {REWARD_LIMIT} solves/day. "
                                "Unclaimed daily quest bonuses still work.", inline=False)
            embed.set_footer(text=f"{day} • {zone} • counts saved, problem links not stored in PostgreSQL")
            return embed

        if await execute(interaction, build, write=True, view=DashboardView(canonical)):
            cached = bot.seen.get(cache_key)
            seen = cached[1] if cached and cached[0] == cache_day else set()
            if len(seen) < 500:
                seen.add(fingerprint)
            bot.seen[cache_key] = (cache_day, seen)
            if len(bot.seen) > 512:
                bot.seen.pop(next(iter(bot.seen)))


@bot.tree.command(name="status", description="Player profile: streaks, counts, Jeancoins, cosmetics, pet, and badges")
@app_commands.guild_only()
async def status(interaction: discord.Interaction, member: discord.Member | None = None):
    await execute(interaction, lambda data, now: profile_embed(data, member or interaction.user, now))


@bot.tree.command(name="today", description="Who's done and who's pending, using each player's own 3:15 AM deadline")
@app_commands.guild_only()
async def today(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: today_embed(data, now))


@bot.tree.command(name="leaderboard", description="See the squad's streaks, XP, Jeancoins, and missed days")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: leaderboard_embed(data, now))


@bot.tree.command(name="history", description="Recent daily solve COUNTS, not a stored list of problems")
@app_commands.guild_only()
async def history(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    def build(data, now):
        state = player(data, target.id)
        rows = [f"**{day}** · " + " / ".join(str(count) for count in values["counts"]) + " (Easy / Medium / Hard)"
                for day, values in sorted(state["recent"].items(), reverse=True)[:14]]
        return card("📊 Recent activity counts", fit_lines(rows, 3500, "No solves counted yet.")
                    + f"\n\nLifetime: **{state['solved']}** solves. Only 30 days of daily counters are retained.", user=target)
    await execute(interaction, build)


@bot.tree.command(name="quest", description="Today's Easy/Medium/Hard sidequests with bonus Jeancoins")
@app_commands.guild_only()
async def quest(interaction: discord.Interaction, difficulty: Difficulty | None = None):
    await execute(interaction, lambda data, now: quest_embed(data, interaction.guild_id, interaction.user, now, difficulty))


@bot.tree.command(name="shop", description="Browse Jean-Luc the Tax Goblin's suspicious boutique")
@app_commands.guild_only()
async def shop(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: shop_embed(player(data, interaction.user.id)), private=True)


async def item_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=f"{item['name']} · {item['price']} Jeancoins", value=key)
            for key, item in ITEMS.items() if current.lower() in (key + item["name"]).lower()][:25]


@bot.tree.command(name="buy", description="Spend Jeancoins on pet food, an egg, cosmetics, or relics")
@app_commands.guild_only()
@app_commands.autocomplete(item=item_autocomplete)
async def purchase(interaction: discord.Interaction, item: str, quantity: app_commands.Range[int, 1, 20] = 1):
    def build(data, now):
        member = player(data, interaction.user.id)
        text = buy(member, item, quantity, now, interaction.user.id)
        return card(SHOPKEEPER, text + f"\n\nWallet: **{member['wallet']} Jeancoins**\n"
                    "Cosmetics/relics: /equip • pet: /pet • food: /feed", GOLD, interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="inventory", description="Inspect your owned items and equipped profile cosmetics")
@app_commands.guild_only()
async def inventory(interaction: discord.Interaction):
    def build(data, now):
        member = player(data, interaction.user.id)
        lines = []
        for key, amount in member["inventory"].items():
            if not amount:
                continue
            line = f"**{ITEMS[key]['name']}** ×{amount}"
            if key in member["equipped"].values():
                line += " · equipped"
            if ITEMS[key]["kind"] in ("aura", "title", "relic"):
                line += f"\n/equip item:{key}"
            lines.append(line)
        return card("🎒 Your goblin-approved inventory", fit_lines(lines, 3500, "Empty pockets. Visit /shop.")
                    + f"\n\n🪙 **{member['wallet']} Jeancoins**", GOLD, interaction.user)
    await execute(interaction, build, private=True)


@bot.tree.command(name="equip", description="Display a title/aura or activate one relic from your inventory")
@app_commands.guild_only()
@app_commands.autocomplete(item=item_autocomplete)
async def equip(interaction: discord.Interaction, item: str):
    await execute(interaction, lambda data, now: card("✨ Drip installed",
                  equip_item(player(data, interaction.user.id), item), GREEN, interaction.user), write=True, private=True)


@bot.tree.command(name="pet", description="Meet your companion, check hunger/evolution, or give it a name")
@app_commands.guild_only()
async def pet(interaction: discord.Interaction, name: str | None = None):
    def build(data, now):
        member = player(data, interaction.user.id)
        if name is not None:
            rename_pet(member, name)
        return pet_embed(member, interaction.user, now)
    await execute(interaction, build, write=name is not None)


@bot.tree.command(name="feed", description="Give your coding companion a Byte Biscuit")
@app_commands.guild_only()
async def feed(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: card("🍪 Snack time",
                  feed_pet(player(data, interaction.user.id), now), GREEN, interaction.user), write=True)


dungeon = app_commands.Group(name="dungeon", description="Fight the Bug Below, draft perks, and bank loot", guild_only=True)


@dungeon.command(name="start", description="Start today's roguelite run; solves advance rooms")
async def dungeon_start(interaction: discord.Interaction, role: Literal["Warden", "Scavenger", "Mage"] = "Warden"):
    def build(data, now):
        member = player(data, interaction.user.id)
        if not member["active"]:
            raise ValueError("Rejoin the challenge before starting a run.")
        start_run(member, interaction.user.id, challenge_day(now, member["timezone"]), role)
        return dungeon_embed(member, interaction.user)
    await execute(interaction, build, write=True)


@dungeon.command(name="status", description="Inspect your run, health, loot, and offered perks")
async def dungeon_status(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: dungeon_embed(player(data, interaction.user.id), interaction.user), private=True)


@dungeon.command(name="choose", description="Draft an offered perk at floor 3, 6, or 9")
async def dungeon_choose(interaction: discord.Interaction, perk: Literal["ward", "greed", "vampire", "lucky", "scholar"]):
    await execute(interaction, lambda data, now: card("🎲 Build upgraded",
                  choose_perk(player(data, interaction.user.id), perk), PINK, interaction.user), write=True)


@dungeon.command(name="retreat", description="End the run safely and bank all unbanked loot")
async def dungeon_retreat(interaction: discord.Interaction):
    def build(data, now):
        payout = retreat(player(data, interaction.user.id))
        return card("🏃 Tactical disengagement", f"Banked **{payout} Jeancoins**. The goblin calls this 'risk management'.", GOLD, interaction.user)
    await execute(interaction, build, write=True)


@dungeon.command(name="heal", description="Drink owned Debug Juice to restore 4 HP")
async def dungeon_heal(interaction: discord.Interaction):
    await execute(interaction, lambda data, now: card("🧃 Debugged your organs",
                  drink(player(data, interaction.user.id)), GREEN, interaction.user), write=True, private=True)


bot.tree.add_command(dungeon)


@bot.tree.command(name="vibecheck", description="Get a gently unhinged assessment of your coding arc")
@app_commands.guild_only()
@app_commands.checks.cooldown(1, 10, key=lambda interaction: (interaction.guild_id, interaction.user.id))
async def vibecheck(interaction: discord.Interaction, member: discord.Member | None = None):
    target = member or interaction.user
    def build(data, now):
        result = stats(data, target.id, player_day(data, target.id, now))
        if result and result["today"]:
            headline, text, color = "🧑‍🍳 Certified chef", "Submitted already? Suspiciously responsible behavior. Your pet is updating its opinion of you.", GREEN
        elif result and result["streak"]:
            headline, text, color = "🔥 Streak on a cliffhanger", "Your streak is buffering. A quick /submit should fix the connection.", GOLD
        else:
            headline, text, color = "🌱 Comeback arc loading", "A missed day is a plot point, not the whole movie. Your next solve starts the sequel.", PURPLE
        return card(headline, text + "\n\n/quest for a mission. /dungeon start for questionable life choices.", color, target)
    await execute(interaction, build)


@bot.tree.command(name="reminders", description="Enable or mute your personal reminder pings")
@app_commands.guild_only()
async def reminders(interaction: discord.Interaction, enabled: bool):
    def build(data, now):
        player(data, interaction.user.id)["reminders"] = enabled
        return card("🔔 Reminder preference saved", "Nudges enabled. We will only ping when you're pending." if enabled else
                    "Nudges muted. The goblin will judge you silently.", user=interaction.user)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="reminder_settings", description="Set local reminder hours for all players, or turn reminders off")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def reminder_settings(interaction: discord.Interaction, enabled: bool = True,
                            first_hour: app_commands.Range[int, 0, 23] = 20,
                            final_hour: app_commands.Range[int, 0, 23] = 2):
    def build(data, now):
        if first_hour == 3 or final_hour == 3:
            raise ValueError("Avoid the 3 AM hour: it straddles the 3:15 cutoff. Use 2 for the last nudge.")
        slots = [f"{first_hour:02}:00", "02:15" if final_hour == 2 else f"{final_hour:02}:00"]
        slots = sorted(set(slots), key=lambda slot: (int(slot[:2]) * 60 + int(slot[3:]) - 195) % 1440)
        data["reminders_enabled"], data["reminder_times"] = enabled, slots
        for member in data["members"].values():
            member["reminder_marks"] = {slot: value for slot, value in member["reminder_marks"].items() if slot in slots}
        return card("🔔 Nudge schedule saved", f"{', '.join(slots)} in each player's timezone."
                    if enabled else "Server reminders disabled.", GREEN)
    await execute(interaction, build, write=True, private=True)


@bot.tree.command(name="storage", description="Admin: check PostgreSQL size against the 50 MB budget")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def storage(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        usage = await bot.store.usage()
        embed = card("🗄️ Compact PostgreSQL storage",
                     f"Entire database: **{usage['database_bytes'] / 1024**2:.2f} MiB**\n"
                     f"Bot tables + indexes: **{usage['bot_bytes'] / 1024**2:.2f} MiB**\n"
                     f"Player rows: **{usage['players']}**\n\n"
                     "No solve URLs, IDs, lists, or purchase logs. One compact snapshot per player; 30 days of daily counts.\n"
                     "New joins are blocked above 45 MiB to leave headroom.")
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as exc:
        log.error("Storage check failed (%s).", type(exc).__name__)
        await interaction.followup.send("Could not query storage usage. Check the database connection.", ephemeral=True)


@bot.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You need Manage Server permission for that command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = f"The goblin is still processing. Try again in {error.retry_after:.0f} seconds."
    else:
        command = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
        original = log_failure(f"Slash command /{command} failed", error)
        message = f"Something went wrong ({type(original).__name__}). The full error is in the bot logs."
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
