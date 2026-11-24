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
- **Automatic scrim stats.** When a [NeatQueue](https://neatqueue.com) game finishes, the bot posts a results image with per-player kills, damage and survev.de impact score, the series score, a timeline of who won each game, and each team's roster. On a finished series, the player with the best impact among those who played every game gets an **MVP** tag. A **Game breakdown** button under it shows every game on its own.
- **Player profiles, history, inventory, and shop viewers** rendered as images, so nobody has to open the site. Profiles and comparisons also show each player's survev.de Squad tier.
- **Hall of Fame** for the best single-queue performances ever recorded (finished queues only).
- **Catch-up on restart.** If the bot was offline when a queue finished, it finds the missed matches and posts them when it comes back.

## Commands

### Getting started

| Command | What it does |
| :--- | :--- |
| `/verify` | Links your survev.de account through a quick authorization link. Required for every stat command. |

### Leaderboards

| Command | What it does |
| :--- | :--- |
| `/leaderboard_old` | *(Legacy)* Top players over the last 7 days. Switch to the last 30 days with the Weekly/Monthly buttons, and pick the stat (kills, wins, games, damage) from the dropdown. Results are reused for 5 minutes, so switching back and forth is instant. |
| `/leaderboard_season [month]` | 4v4 queue leaderboard for a calendar month (`YYYY-MM`, defaults to the current month), ranked by average damage, impact, or kills per game (pick from the dropdown). Use the Previous/Next buttons to page through everyone, 10 per page. |
| `/leaderboard_fries` | Ranks linked players by Golden Fries balance. |

### Players

| Command | What it does |
| :--- | :--- |
| `/profile [member]` | Opens on an **Overview**: 4v4 play time, average damage and impact (all-time and this month), and the player's survev.de Squad tier (for example `B- · rating 29 · EU`; needs a linked account). In a server, the **History** button shows their last 10 finished queues there as a card with a trend chart; switch the chart between Damage, Impact, and Kills with the buttons below it. |
| `/compare <member_a> [member_b]` | Side-by-side stat card for two players: survev.de wins, kills per game, damage, Squad tier and inventory worth, then NeatQueue 4v4 play time and average damage. The higher value on each row is green. Leave out `member_b` to compare against yourself; your own column is always on the left. A stat that couldn't be loaded shows `N/A` instead of a zero. |
| `/inventory [member]` | Paged inventory image with a rarity filter and the inventory's total value. Each card shows the item's icon (the same art as the survev.de locker) and its rarity in words and colour. Pages reuse the inventory for 5 minutes, then fetch it again. |
| `/goldenfries [member]` | Golden Fries balance. |
| `/market [member]` | Shop offers image with the player's Golden Fries balance and buttons to switch between Daily, Weekly, and All. Every offer shows each item's icon and name; Daily includes survev.de's two Premium daily offers. The buttons reuse the offers for 5 minutes, then fetch them again. |

### Queues

| Command | What it does |
| :--- | :--- |
| `/queue_stats <match_id>` | Builds the results image for a NeatQueue game number. Ongoing queues show **IN PROGRESS** above the score and **Team N leads** (or **No team leading**). Substitutes and players who left are shown on the team they played for, with the games they played (for example `1/7`). The Game breakdown button only appears once the queue has finished. |
| `/hall_of_fame` | Best records: most kills and most average damage in a queue, most damage in a single game, longest queue. Only finished queues of 4+ games can set records. |

You can also **type `score?` or mention @ctierbot in a `queue-<number>` channel** (for example, `queue-4607`) to get that queue's current stats as a public reply. `score?` needs no mention and can appear anywhere in a message (for example, "what is the score?"), ignoring capitalization. It must be the word `score` followed directly by `?`, so `scoreboard?` or `score` without a question mark does not trigger it. For the mention shortcut, select the actual bot mention in Discord. Ongoing queues show **IN PROGRESS**; finished queues show final results and the Game breakdown button. These channels do not need individual `/setup` registration, but the bot needs permission to view them, send replies, and attach files. The same linked-player and queue-data requirements as `/queue_stats` apply.

Both shortcuts share a **30-second cooldown per channel** after a stats or lookup-error reply. Requests during the cooldown or while a lookup is running are silently ignored, with no wait message or DM. Bot/webhook messages, NeatQueue's recurring winner-vote panels, and implicit reply pings do not trigger these shortcuts. Checking stats this way does not mark the queue as automatically posted. The `score?` shortcut requires the Message Content Intent described in setup below.

In any server channel, you can also type **`?c <queue_number>`** or **`?c<queue_number>`** (for example `?c 4123`, `?c4123`, or `?c #4123`, case-insensitive) at the start of a message to pull stats for any queue number without a cooldown, so you can check multiple queues back-to-back. The bot answers only where it can send messages, attach files and read message history (in threads: send messages in threads); elsewhere it ignores the command without looking anything up. It doesn't work in DMs, because a queue number belongs to a server.

### Reactions

The bot reacts with its animated **cooly** emoji to any message that contains the word `cooly` anywhere, in any capitalization (`Cooly`, `COOLY`, `coolyyy`), in every channel it can see. Messages from bots are ignored. The emoji is an application emoji uploaded in the Discord Developer Portal, so it works in every server. Where the bot lacks **Add Reactions** or **Read Message History**, it skips the reaction silently. More words and emojis can be added in `KEYWORD_REACTIONS` in `bot_config.py`.

### Admin

Admin-only commands are hidden from members without the **Administrator** permission.

| Command | What it does |
| :--- | :--- |
| `/setup <channel>` | Starts tracking NeatQueue results in a channel. Run it once per channel. |
| `/unsetup <channel>` | Stops tracking that channel. |
| `/reset_hall_of_fame` | Deletes every Hall of Fame record, in every server that uses the bot. Only the bot's owner can run it, and it asks for confirmation first. |

## How queue tracking works

1. An admin runs `/setup` on the channel where NeatQueue posts its results.
2. When NeatQueue announces a winner (or finalizes its results panel), the bot picks up the queue number.
3. It looks up the queue on NeatQueue, then uses the linked players' own survev.de match history to find the games played during that queue.
4. It pulls the public scoreboard for each game and keeps only games between the queue's two teams (each with at least 2 players). survev.de renumbers teams every game, so each game's teams are matched to NeatQueue's Team 1 and Team 2 from where the linked roster players played. That way substitutes count for the team they actually played with. Then it posts the image.
5. For a finished queue it also saves each game's numbers (kept for 90 days), so the **Game breakdown** button can show them later, even after a restart. It opens a private message that only the person who clicked can see, with one card per game, Previous/Next buttons, and a Jump to game menu that names each game's winner and its top average damage.

You need **at least one linked player** in a queue for the bot to find its games. Players who haven't linked still show up under their survev.de name, and the result message has a button so they can link on the spot.

### The Impact column

**Impact** is survev.de's per-game impact score (0–100). The result image shows each player's average over the games survev.de scored; a `-` means none of their games were scored (for example they barely fought). survev.de only scores impact on some maps, so the column simply doesn't appear when nobody was scored.

### Squad tier

`/profile` and `/compare` show the player's survev.de **Squad tier** (`F E D C B- B A- A S`, based on their average impact score) and rating. It is looked up live with the player's own link, cached for 5 minutes, and shows `Unranked` until they have rated Squad games.

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
| `API_CONCURRENCY_LIMIT` | `10` | How many survev.de requests the bot sends at once. survev.de's rate limits count requests per minute, so this only changes how quickly a lookup finishes. |
| `QUEUE_STATS_LOG_MAX_FILES` | `200` | Maximum number of queue lookup diagnostic JSON logs retained in `log/`. |
| `LOG_LEVEL` | `INFO` | Console log level. `DEBUG` also logs every button and menu click. |
| `DB_PATH` | `leaderboard.db` | Where the SQLite database lives. The missing parent folder is created. The Docker image sets `/app/data/leaderboard.db`. |
| `CONSOLE_PORT` | `0` (off) | Loopback port for `console.py`, the host console in a container. The Docker image sets `8765`. |
| `LEADERBOARD_BANNER_URL` | a Giphy GIF | Banner image on the weekly and monthly leaderboards. |
| `QUEUE_FONT_PATH` / `QUEUE_FONT_BOLD_PATH` | auto-detected | Custom fonts for the generated images. |

> Keep `.env` private. It's already in `.gitignore`, so it won't be committed.

### 4. Run it

```bash
python leaderboard_bot.py
```

Slash commands are synced on startup. The SQLite database (`leaderboard.db`) and a `log/` folder are created automatically in the working directory. Every queue lookup (`/queue_stats`, automatic result posts, restart catch-up, and `score?` or mention replies) writes a diagnostic JSON log to `log/` (without tokens or raw API payloads) to help troubleshoot live vs. final score lookups. Each log's `event` field says which of these started it.

Then, in Discord, run `/setup` in your NeatQueue results channel and ask players to `/verify`.

When you deploy, upload the `assets/` folder with the code. Without it, `/inventory` and `/market` still work, just without item icons.

### Running in Docker (Coolify)

The `Dockerfile` builds a Python 3.13 image with the code, fonts and `assets/`. `.dockerignore` keeps `.env`, local databases, logs and tests out of it. Production runs it on Coolify, built from this repo's `main` branch:

- Set the `.env` variables as environment variables in Coolify instead of a `.env` file.
- Mount persistent storage at **`/app/data`** (a folder, so SQLite's journal file sits next to the database). The image sets `DB_PATH=/app/data/leaderboard.db`.
- No port, domain or HTTP health check: the bot only makes outbound connections.
- Use container naming **"Consistent name (no rolling updates)"**. A rolling update starts the new container before stopping the old one, so two bots would run on the same token and database for a moment.
- The bot closes cleanly on `SIGTERM`, so stops and redeploys don't wait out the grace period.
- `log/` is inside the container and starts empty after each redeploy.
- A daily Coolify scheduled task runs `python backup_db.py`: a consistent SQLite copy into `/app/data/backups/`, newest 14 kept (`BACKUP_KEEP`). It lives on the same disk, so it covers bad writes, not losing the VPS.
- `requirements.txt` is pinned to the versions verified in production. Upgrade by changing a pin, testing, then pushing.

### Updating item icons

The item icons in `assets/items/` are PNGs exported from a local survev.de checkout, so the bot doesn't need an SVG renderer. When skins are added to the game, re-export them from the survev.de folder (it provides `tsx`, `sharp`, and the item definitions):

```bash
cd ../survev.de
node_modules/.bin/tsx ../C-TierBot/tools/export_item_icons.mts
```

This rewrites `assets/items/` with one 128 px icon per cosmetic item plus `manifest.json` (item names, kinds, rarities, and the survev.de commit they came from; `-dirty` means uncommitted survev.de changes were included). Outfits that use the generic shirt are drawn as the in-game character (backpack, body, accessory, hands), like the survev.de locker.

## Host console

While the bot is running in a terminal, you can type commands straight into it to send messages, list servers, check status, or run SQL. In a container there is no terminal attached to the bot, so open a shell in the container (Coolify: the app's **Terminal** tab) and run `python console.py` for the same commands. See [HOST_COMMANDS.md](HOST_COMMANDS.md) for the full list.

## Project layout

| File | Role |
| :--- | :--- |
| `leaderboard_bot.py` | Entry point: slash commands, event handlers, startup. |
| `bot_config.py` | Environment variables, constants, and the shared bot instance. |
| `http_client.py` | The one shared HTTP session and the bot-wide survev.de request limit. |
| `db.py` | SQLite schema and every read/write helper. |
| `survev_client.py` | survev.de API calls and the account-linking flow. |
| `neatqueue_client.py` | NeatQueue API calls and the logic that matches a queue to its survev.de games. |
| `queue_stats_service.py` | Queue results, Game breakdown, Hall of Fame tracking, and logging. |
| `leaderboard_service.py` | Leaderboard, inventory, shop, profile, and compare payloads, plus the cached Squad tier lookup. |
| `image_utils.py` | All Pillow-based image rendering. |
| `design_tokens.py` | The cards' colours, text sizes and credit line, named by what they're for. |
| `discord_ui.py` | Buttons, dropdowns, and pagination views. |
| `console_service.py` | The host terminal console, plus the loopback socket `console.py` talks to. |
| `console.py` | Console client for a container: `python console.py` (prompt) or `python console.py <command>`. |
| `Dockerfile`, `.dockerignore` | The container image Coolify builds. |
| `backup_db.py` | Daily database backup, run by a Coolify scheduled task. |
| `assets/items/` | Item icons and `manifest.json` for `/inventory` and `/market`, generated by `tools/export_item_icons.mts`. |

## Tests

```bash
python -m unittest discover tests
```

`tests/test_golden_images.py` compares every card with a saved picture in `tests/golden/`. After an intended visual change, run it with `UPDATE_GOLDEN=1` to save the new pictures, and look at them before committing.

## Support

If the bot is useful to your community, you can chip in on [Ko-fi](https://ko-fi.com/G5R724QGXQ). Bug reports and ideas are welcome as issues.

Stats come from the survev.de and NeatQueue APIs. This project isn't affiliated with either.
