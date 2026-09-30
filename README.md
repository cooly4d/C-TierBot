<div align="center">

# C-TierBot

**Your [survev.de](https://survev.de) stats, right inside Discord. And your scrims, tracked automatically.**

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.4%2B-5865F2?logo=discord&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite-003B57?logo=sqlite&logoColor=white)

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/G5R724QGXQ)

</div>

---

## What it does

C-TierBot links your server's players to their survev.de accounts and then does the boring stuff for you:

- **Leaderboards** for kills, wins, games and damage (weekly and monthly), plus a Golden Fries rich list.
- **Automatic scrim stats.** When a [NeatQueue](https://neatqueue.com) game finishes, the bot posts a results image with per-player kills and damage, the series score, and each team's roster.
- **Player profiles, inventory, and shop viewers** rendered as images, so nobody has to open the site.
- **Hall of Fame** for the best single-queue performances ever recorded.
- **Catch-up on restart.** If the bot was offline when a queue finished, it finds the missed matches and posts them when it comes back.

## Commands

### Getting started

| Command | What it does |
| :--- | :--- |
| `/verify` | Links your survev.de account through a quick authorization link. Required for every stat command. |

### Leaderboards

| Command | What it does |
| :--- | :--- |
| `/leaderboard_weekly` | Top players over the last 7 days. Switch period or stat (kills, wins, games, damage) with the buttons and dropdown. |
| `/leaderboard_monthly` | Same, over the last 30 days. |
| `/leaderboard_season [month]` | Average damage in 4v4 queues for a calendar month (`YYYY-MM`, defaults to the current month). Use the Previous/Next buttons to page through everyone, 10 per page. |
| `/leaderboard_fries` | Ranks linked players by Golden Fries balance. |

### Players

| Command | What it does |
| :--- | :--- |
| `/profile [member]` | Cached NeatQueue stats: 4v4 play time and average damage (all-time and this month). |
| `/compare <member_a> [member_b]` | Side-by-side stat card for two players. Leave out `member_b` to compare against yourself. |
| `/inventory [member]` | Paged inventory image with a rarity filter. |
| `/goldenfries [member]` | Golden Fries balance. |
| `/market daily [member]` | Daily shop offers. |
| `/market weekly [member]` | Weekly shop offers. |

### Queues

| Command | What it does |
| :--- | :--- |
| `/queue_stats <match_id>` | Builds the results image for a NeatQueue game number. Also works while the queue is still in progress. |
| `/queueresults <match_id>` | Alias of `/queue_stats`. |
| `/hall_of_fame` | Best single-queue records: most kills, most average damage, longest queue. |

### Admin

Admin-only commands are hidden from members without the **Administrator** permission.

| Command | What it does |
| :--- | :--- |
| `/setup <channel>` | Starts tracking NeatQueue results in a channel. Run it once per channel. |
| `/unsetup <channel>` | Stops tracking that channel. |
| `/reset_hall_of_fame` | Clears every Hall of Fame record. |

## How queue tracking works

1. An admin runs `/setup` on the channel where NeatQueue posts its results.
2. When NeatQueue announces a winner (or finalizes its results panel), the bot picks up the queue number.
3. It looks up the queue on NeatQueue, then uses the linked players' own survev.de match history to find exactly which games belonged to that queue.
4. It pulls the public scoreboard for each game, groups players by team, and posts the image.

You need **at least one linked player** in a queue for the bot to find its games. Players who haven't linked still show up under their survev.de name, and the result message has a button so they can link on the spot.

## Self-hosting

### Requirements

- Python 3.10 or newer
- A Discord bot application
- survev.de OAuth client credentials
- A NeatQueue API token (only needed for queue tracking)

### 1. Clone and install

```bash
git clone https://github.com/<your-username>/C-TierBot.git
cd C-TierBot

python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# Linux / macOS: source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Create the Discord bot

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create an application, add a bot, and copy its token.
2. Under **Bot → Privileged Gateway Intents**, turn on both:
   - **Message Content Intent**
   - **Server Members Intent**
3. Invite it with the `bot` and `applications.commands` scopes. It needs permission to view channels, send messages, attach files, embed links, and read message history in the channels you track.

### 3. Configure `.env`

Create a `.env` file next to `leaderboard_bot.py`:

```env
DISCORD_BOT_TOKEN=your-bot-token
SURVEV_CLIENT_ID=your-survev-client-id
SURVEV_CLIENT_SECRET=your-survev-client-secret
NEATQUEUE_API_TOKEN=your-neatqueue-token
```

Optional settings:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `NEATQUEUE_BOT_ID` | `857633321064595466` | The NeatQueue bot's user ID, used to recognize its result messages. |
| `QUEUE_RESULT_FETCH_DELAY_SECONDS` | `5` | Wait before fetching stats after a queue ends, so survev.de has time to save the games. |
| `QUEUE_MATCH_FALLBACK_DURATION_MINUTES` | `10` | Last-resort match window when NeatQueue gives no usable end time. |
| `LEADERBOARD_BANNER_URL` | a Giphy GIF | Banner image on the weekly and monthly leaderboards. |
| `QUEUE_FONT_PATH` / `QUEUE_FONT_BOLD_PATH` | auto-detected | Custom fonts for the generated images. |

> Keep `.env` private. It's already in `.gitignore`, so it won't be committed.

### 4. Run it

```bash
python leaderboard_bot.py
```

Slash commands are synced on startup. The SQLite database (`leaderboard.db`) and a `log/` folder are created automatically in the working directory.

Then, in Discord, run `/setup` in your NeatQueue results channel and ask players to `/verify`.

## Host console

While the bot is running in a terminal, you can type commands straight into it to send messages, list servers, check status, or run SQL. See [HOST_COMMANDS.md](HOST_COMMANDS.md) for the full list.

## Project layout

| File | Role |
| :--- | :--- |
| `leaderboard_bot.py` | Entry point: slash commands, event handlers, startup. |
| `bot_config.py` | Environment variables, constants, and the shared bot instance. |
| `db.py` | SQLite schema and every read/write helper. |
| `survev_client.py` | survev.de API calls and the account-linking flow. |
| `neatqueue_client.py` | NeatQueue API calls and the logic that matches a queue to its survev.de games. |
| `queue_stats_service.py` | Queue results, Hall of Fame tracking, and logging. |
| `leaderboard_service.py` | Leaderboard, inventory, shop, and compare payloads. |
| `image_utils.py` | All Pillow-based image rendering. |
| `discord_ui.py` | Buttons, dropdowns, and pagination views. |
| `console_service.py` | The host terminal console. |

## Tests

```bash
python -m unittest discover tests
```

## Support

If the bot is useful to your community, you can chip in on [Ko-fi](https://ko-fi.com/G5R724QGXQ). Bug reports and ideas are welcome as issues.

Stats come from the survev.de and NeatQueue APIs. This project isn't affiliated with either.