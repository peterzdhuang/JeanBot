# JeanBot

JeanBot is a Discord LeetCode Arena bot. It tracks daily solve counts and streaks, then adds quests, Jeancoins, collectible pets, and a small dungeon game. It stores totals and bounded game state in PostgreSQL; it does not persist solved problem URLs or submission IDs.

## Requirements

- Python 3.10 or newer
- A Discord application and bot token
- A PostgreSQL database (a hosted PostgreSQL provider works)

## 1. Create and invite the Discord bot

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot**, create the bot user, and copy its token. Keep the token private.
3. Under **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes.
4. Grant the bot **View Channels**, **Send Messages**, and **Embed Links**. Administrator and Message Content intent are not needed.
5. Open the generated URL and invite the bot to your server.

## 2. Configure JeanBot

Clone the repository and install dependencies in a virtual environment:

```sh
git clone https://github.com/peterzdhuang/JeanBot.git
cd JeanBot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

Create a private environment file from the example:

```sh
cp .env.example .env
```

Set these values in `.env`:

- `DISCORD_TOKEN`: the bot token from the Developer Portal
- `DATABASE_URL`: the connection URL from your PostgreSQL provider, for example `postgresql://username:password@host:5432/database`

URL-encode special characters in the database username or password. Use the SSL settings required by your database provider. Never commit `.env` or share your bot token/database credentials. If a credential is exposed, rotate it.

## 3. Start the bot

```sh
python bot.py
```

JeanBot creates its PostgreSQL tables and syncs its slash commands when it starts. Keep one bot process running for a server. In Discord, configure a channel and join with your timezone:

```text
/setup channel:#leetcode timezone_name:America/Los_Angeles
/join timezone_name:America/Los_Angeles
```

Try `/quest`, `/submit`, `/status`, and `/leaderboard` to get started. Challenge days run from 3:15 AM to 3:15 AM in each player's timezone. Use an IANA timezone such as `America/Toronto`, `America/Phoenix`, or `UTC`.

## Commands

- Tracking: `/join`, `/leave`, `/submit`, `/status`, `/today`, `/history`, `/leaderboard`
- Daily activities: `/quest`, `/vibecheck`
- Shop and pets: `/shop`, `/buy`, `/inventory`, `/equip`, `/pet`, `/feed`
- Dungeon: `/dungeon`
- Server setup: `/setup`, `/storage`, `/reminder_settings`

## Development

Run the offline test suite with:

```sh
python -m unittest -v
python -m py_compile bot.py tracker.py catalog.py game.py storage.py
```

Tests use a fake database connection; they do not validate a live PostgreSQL instance or Discord command registration.

## Data and privacy

The database keeps player totals, recent daily counts, preferences, and compact game state. It does not retain submitted problem links, slugs, or submission IDs. Discord solve announcements can include the link in Discord; JeanBot does not copy it into PostgreSQL. See the source code and tests before deploying, and back up your database through your provider.

Jeancoins are in-game points and have no real-world monetary value.
