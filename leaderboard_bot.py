import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import re
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

LEADERBOARD_BANNER_URL = os.getenv("LEADERBOARD_BANNER_URL", "https://media.giphy.com/media/3o6ZtpxSZbQ2zYpH0A/giphy.gif")

# --- CONFIGURATION ---

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SURVEV_CLIENT_ID = os.getenv("SURVEV_CLIENT_ID")
SURVEV_CLIENT_SECRET = os.getenv("SURVEV_CLIENT_SECRET")
NEATQUEUE_API_TOKEN = os.getenv("NEATQUEUE_API_TOKEN")
NEATQUEUE_SERVER_ID = os.getenv("NEATQUEUE_SERVER_ID")
NEATQUEUE_API_BASE = "https://api.neatqueue.com/api/v1"
NEATQUEUE_BOT_ID = int(os.getenv("NEATQUEUE_BOT_ID", "857633321064595466"))
QUEUE_RESULT_FETCH_DELAY_SECONDS = int(os.getenv("QUEUE_RESULT_FETCH_DELAY_SECONDS", "5"))
# Last-resort window length when NeatQueue gives us no usable end-of-match signal at all.
QUEUE_MATCH_FALLBACK_DURATION_MS = int(os.getenv("QUEUE_MATCH_FALLBACK_DURATION_MINUTES", "10")) * 60 * 1000
QUEUE_FONT_PATHS = [
    os.getenv("QUEUE_FONT_PATH"),
    "fonts/QuattrocentoSans-Regular.ttf",
    "QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]
QUEUE_FONT_BOLD_PATHS = [
    os.getenv("QUEUE_FONT_BOLD_PATH"),
    "fonts/QuattrocentoSans-Bold.ttf",
    "QuattrocentoSans-Bold.ttf",
    "/usr/share/fonts/truetype/quattrocento/QuattrocentoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
#all supposed to be environment variables by cba


# Database initialization
conn = sqlite3.connect("leaderboard.db")
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        discord_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL,
        slug TEXT,
        username TEXT
    )
''')
# Older DBs created before slug/username existed — add them on if missing.
for column_def in ("slug TEXT", "username TEXT"):
    try:
        cursor.execute(f"ALTER TABLE users ADD COLUMN {column_def}")
    except sqlite3.OperationalError:
        pass
cursor.execute('''
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        channel_id INTEGER NOT NULL,
        last_updated TEXT NOT NULL
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS processed_matches (
        guild_id INTEGER NOT NULL,
        match_id TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, match_id)
    )
''')
conn.commit()

# NeatQueue's final results announcement, e.g. "🏆 Winner For Queue#3674 🏆" — already final when posted.
QUEUE_WINNER_TITLE_PATTERN = re.compile(r"Winner For Queue#(\d+)", re.IGNORECASE)
# NeatQueue's admin queue panel, e.g. "Results for Queue#3889" — posted at queue start with no result yet,
# then edited in place once the queue finishes. Only the edit carries a real result.
QUEUE_PANEL_TITLE_PATTERN = re.compile(r"Results for Queue#(\d+)", re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    bot.add_view(queue_result_view)
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await backfill_missed_queue_results()

# Helper: Save User Token (+ their survev.de slug/username, so we can recognize them by slug later)
def save_token(discord_id: int, token: str, slug: str | None = None, username: str | None = None):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute(
            "INSERT OR REPLACE INTO users (discord_id, access_token, slug, username) VALUES (?, ?, ?, ?)",
            (discord_id, token, slug, username)
        )

# Helper: Get All User Tokens
def get_all_users():
    with sqlite3.connect("leaderboard.db") as c:
        return c.execute("SELECT discord_id, access_token FROM users").fetchall()

# Helper: Look up which verified Discord user owns a given survev.de slug, if any
def get_discord_id_by_slug(slug: str):
    with sqlite3.connect("leaderboard.db") as c:
        row = c.execute("SELECT discord_id FROM users WHERE slug = ?", (slug,)).fetchone()
        return row[0] if row else None

# Helper: Get Single User Token
def get_user_token(discord_id: int):
    with sqlite3.connect("leaderboard.db") as c:
        row = c.execute("SELECT access_token FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        return row[0] if row else None

# Helper: Set the channel a guild wants NeatQueue results tracked in
def set_guild_queue_channel(guild_id: int, channel_id: int):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, channel_id, last_updated) VALUES (?, ?, ?)",
            (guild_id, channel_id, datetime.now(timezone.utc).isoformat())
        )

# Helper: Get the channel configured for a guild, if any
def get_guild_queue_channel(guild_id: int):
    with sqlite3.connect("leaderboard.db") as c:
        row = c.execute("SELECT channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
        return row[0] if row else None

# Helper: Get every guild's configured channel + the last time its queue results were caught up on
def get_all_guild_settings():
    with sqlite3.connect("leaderboard.db") as c:
        return c.execute("SELECT guild_id, channel_id, last_updated FROM guild_settings").fetchall()

# Helper: Mark a guild as caught up as of the given timestamp, without touching its channel
def update_guild_last_updated(guild_id: int, timestamp_iso: str):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute("UPDATE guild_settings SET last_updated = ? WHERE guild_id = ?", (timestamp_iso, guild_id))

# Helper: Record that a match's stats were posted. Returns False if it was already recorded (skip re-posting).
def try_mark_match_processed(guild_id: int, match_id: str) -> bool:
    with sqlite3.connect("leaderboard.db") as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO processed_matches (guild_id, match_id, processed_at) VALUES (?, ?, ?)",
            (guild_id, str(match_id), datetime.now(timezone.utc).isoformat())
        )
        return cur.rowcount > 0


def resolve_queue_font_path(paths: list[str | None]) -> str | None:
    for path in paths:
        if not path:
            continue
        if os.path.isfile(path):
            return path
        try:
            ImageFont.truetype(path, 12)
            return path
        except Exception:
            continue
    return None

QUEUE_FONT_PATH = resolve_queue_font_path(QUEUE_FONT_PATHS)
QUEUE_FONT_BOLD_PATH = resolve_queue_font_path(QUEUE_FONT_BOLD_PATHS) or QUEUE_FONT_PATH


def load_font(size: int, weight: str = "regular"):
    font_path = QUEUE_FONT_BOLD_PATH if weight in ("bold", "semibold") else QUEUE_FONT_PATH
    if font_path is not None:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass

    if weight in ("bold", "semibold"):
        fallback_names = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "FreeSansBold.ttf")
    else:
        fallback_names = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf")

    for fallback in fallback_names:
        try:
            return ImageFont.truetype(fallback, size)
        except Exception:
            continue

    return ImageFont.load_default()


async def resolve_queue_user_display_names(teams: list[list[dict]], client: discord.Client):
    for team in teams:
        for entry in team:
            discord_id = entry.get("discord_id")
            if discord_id is None:
                # No /verify'd Discord user owns this slug — show their real survev.de name instead.
                entry["display_name"] = entry.get("username") or "Unknown"
                continue

            member = next((m for m in client.get_all_members() if m.id == discord_id), None)
            if member is not None:
                entry["display_name"] = member.display_name
                continue

            user = client.get_user(discord_id)
            if user is None:
                try:
                    user = await client.fetch_user(discord_id)
                except Exception:
                    user = None

            if user is not None:
                entry["display_name"] = user.name
            else:
                entry["display_name"] = f"Player {discord_id}"


async def get_user_display_name(client: discord.Client, discord_id: int) -> str:
    member = next((m for m in client.get_all_members() if m.id == discord_id), None)
    if member is not None:
        return member.display_name

    user = client.get_user(discord_id)
    if user is None:
        try:
            user = await client.fetch_user(discord_id)
        except Exception:
            user = None

    if user is not None:
        return user.name
    return f"Player {discord_id}"


# --- Queue result image styling (module-level so any renderer can reuse/tweak it) ---
QUEUE_IMG_WIDTH = 1200
QUEUE_IMG_PADDING = 36
QUEUE_IMG_HEADER_HEIGHT = 170
QUEUE_IMG_TEAM_HEADER_HEIGHT = 60
QUEUE_IMG_ROW_HEIGHT = 60

QUEUE_IMG_BG = (18, 24, 37)
QUEUE_IMG_HEADER_BG = (24, 33, 55)
QUEUE_IMG_HEADER_GRADIENT_END = (34, 48, 73)
QUEUE_IMG_ROW_ALT = (28, 37, 55)
QUEUE_IMG_TEXT = (235, 237, 240)
QUEUE_IMG_MUTED = (168, 183, 207)
QUEUE_IMG_ACCENT = (255, 165, 50)
QUEUE_IMG_WIN = (108, 199, 128)
QUEUE_IMG_LOSE = (214, 96, 96)
QUEUE_IMG_WIN_BADGE = (34, 106, 72)
QUEUE_IMG_TEAM_SCORE_BADGE_BG = (18, 84, 54)
QUEUE_IMG_TEAM_SCORE_BADGE_TEXT = (235, 237, 240)

# Column offsets as a fraction of a team panel's width: Player, Kills, Damage, Avg Damage
QUEUE_IMG_COLUMN_RATIOS = [0.0, 0.40, 0.58, 0.76]
QUEUE_IMG_COLUMN_LABELS = ["Player", "Kills", "Dmg", "Avg Dmg"]


def generate_queue_result_image(match_id: str, teams: list[list[dict]], winning_team_index: int | None) -> BytesIO:
    num_teams = max(len(teams), 1)
    max_rows = max((len(team) for team in teams), default=0)

    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams
    panel_x_positions = [QUEUE_IMG_PADDING + i * (panel_width + QUEUE_IMG_PADDING) for i in range(num_teams)]
    columns = [round(ratio * panel_width) for ratio in QUEUE_IMG_COLUMN_RATIOS]

    rows_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING + QUEUE_IMG_TEAM_HEADER_HEIGHT
    height = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_PADDING

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(46, "bold")
    subtitle_font = load_font(26, "bold")
    team_header_font = load_font(24, "bold")
    header_font = load_font(18, "bold")
    body_font = load_font(20)
    body_font_bold = load_font(20, "bold")
    footer_font = load_font(15)

    # Top banner with subtle gradient
    for y in range(QUEUE_IMG_HEADER_HEIGHT):
        ratio = y / max(1, QUEUE_IMG_HEADER_HEIGHT - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 26), f"NeatQueue #{match_id}", font=title_font, fill=QUEUE_IMG_TEXT)

    if winning_team_index is not None and 0 <= winning_team_index < num_teams:
        winner_text = f"Team {winning_team_index + 1} Wins"
        winner_color = QUEUE_IMG_WIN
    else:
        winner_text = "Result unknown"
        winner_color = QUEUE_IMG_MUTED

    winner_bbox = draw.textbbox((QUEUE_IMG_PADDING, 80), winner_text, font=subtitle_font)
    draw.text((QUEUE_IMG_PADDING, 80), winner_text, font=subtitle_font, fill=QUEUE_IMG_WIN)

    score_font = load_font(44, "bold")
    team_scores = [sum(entry["stats"].get("wins", 0) for entry in team if entry["stats"]) for team in teams]
    if num_teams == 2:
        left_text = str(team_scores[0])
        right_text = str(team_scores[1])
        separator_text = " - "
        left_color = QUEUE_IMG_TEXT
        right_color = QUEUE_IMG_TEXT

        if winning_team_index is not None:
            if winning_team_index == 0:
                left_color = QUEUE_IMG_WIN
                right_color = QUEUE_IMG_LOSE
            elif winning_team_index == 1:
                left_color = QUEUE_IMG_LOSE
                right_color = QUEUE_IMG_WIN

        left_width = draw.textbbox((0, 0), left_text, font=score_font)[2]
        sep_width = draw.textbbox((0, 0), separator_text, font=score_font)[2]
        right_width = draw.textbbox((0, 0), right_text, font=score_font)[2]
        total_width = left_width + sep_width + right_width

        section_left = winner_bbox[2] + 24
        section_right = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
        x_pos = section_left + max(0, (section_right - section_left - total_width) / 2) + 12
        y_pos = 74

        draw.text((x_pos, y_pos), left_text, font=score_font, fill=left_color)
        draw.text((x_pos + left_width, y_pos), separator_text, font=score_font, fill=QUEUE_IMG_TEXT)
        draw.text((x_pos + left_width + sep_width, y_pos), right_text, font=score_font, fill=right_color)
    else:
        score_text = " / ".join(str(score) for score in team_scores)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        section_left = winner_bbox[2] + 24
        section_right = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
        x_pos = section_left + max(0, (section_right - section_left - (score_bbox[2] - score_bbox[0])) / 2) + 12
        y_pos = 74
        draw.text((x_pos, y_pos), score_text, font=score_font, fill=QUEUE_IMG_TEXT)

    draw.text((QUEUE_IMG_PADDING, 118), "Data courtesy of NeatQueue & survev.de APIs :)", font=footer_font, fill=QUEUE_IMG_MUTED)

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING

    for team_index in range(num_teams):
        team_players = teams[team_index] if team_index < len(teams) else []
        x0 = panel_x_positions[team_index]
        is_winner = winning_team_index == team_index
        team_color = QUEUE_IMG_WIN if is_winner else (QUEUE_IMG_LOSE if winning_team_index is not None else QUEUE_IMG_MUTED)

        team_label = f"Team {team_index + 1}"
        team_label_width = draw.textbbox((0, 0), team_label, font=team_header_font)[2]
        team_label_x = x0 + (panel_width - team_label_width) / 2
        draw.text((team_label_x, panel_top), team_label, font=team_header_font, fill=team_color)

        header_y = panel_top + QUEUE_IMG_TEAM_HEADER_HEIGHT - 24
        col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [panel_width - columns[-1]]
        for col_idx, label in enumerate(QUEUE_IMG_COLUMN_LABELS):
            label_x = x0 + columns[col_idx]
            label_width = draw.textbbox((0, 0), label, font=header_font)[2]
            if col_idx != 0 and col_widths[col_idx] > label_width:
                label_x += (col_widths[col_idx] - label_width) / 2
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

        if not team_players:
            draw.text((x0, rows_top), "No players", font=body_font, fill=QUEUE_IMG_MUTED)

        for row_index, entry in enumerate(team_players):
            row_top = rows_top + row_index * QUEUE_IMG_ROW_HEIGHT
            row_bottom = row_top + QUEUE_IMG_ROW_HEIGHT - 8
            if row_index % 2 == 0:
                draw.rectangle([x0, row_top, x0 + panel_width, row_bottom], fill=QUEUE_IMG_ROW_ALT)

            stats = entry["stats"]
            player_label = entry.get("display_name") or entry.get("username") or "Unknown"
            is_guest = entry.get("guest", False)

            if is_guest:
                # No /verify'd Discord user owns this survev.de account — the name shown is already
                # their real in-game username, just flag that they're not linked.
                player_label = f"{player_label} (unlinked)"

            games = stats["games"]
            avg_damage = stats["damage"] / games if games else 0

            row_values = [
                player_label,
                str(stats["kills"]),
                f"{stats['damage']:,}",
                f"{avg_damage:,.0f}"
            ]
            for col_idx, value in enumerate(row_values):
                fill = QUEUE_IMG_ACCENT if col_idx == 2 else QUEUE_IMG_TEXT
                font = body_font_bold if col_idx in (0, 2, 3) else body_font
                cell_x = x0 + columns[col_idx]
                if col_idx == 0:
                    draw.text((cell_x, row_top + 14), value, font=font, fill=fill)
                else:
                    value_width = draw.textbbox((0, 0), value, font=font)[2]
                    centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                    draw.text((centered_x, row_top + 14), value, font=font, fill=fill)

        panel_bottom = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT
        draw.rectangle([x0 - 8, panel_top - 8, x0 + panel_width + 8, panel_bottom], outline=team_color, width=2)

    footer_text = "Link your survev.de account with /verify to appear in future leaderboards!"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ------------------------------------------------------------------
# 1. VERIFICATION COMMAND
# ------------------------------------------------------------------
async def run_survev_verification(discord_user_id: int, send_update):
    """Runs the survev.de OAuth device-code flow, calling `send_update(**kwargs)` (a discord.py-style
    send with `content=`/`embed=`) for each step. Shared by /verify and the "not showing up?" button so
    both paths (in-channel vs DM) stay in sync."""
    device_url = "https://survev.de/api/oauth/device/code"
    token_url = "https://survev.de/api/oauth/token"

    payload = {
        "clientId": SURVEV_CLIENT_ID,
        "clientSecret": SURVEV_CLIENT_SECRET,
        "scope": ["read:discord", "read:stats"]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(device_url, json=payload) as resp:
            if resp.status != 200:
                error_details = await resp.text()
                print(f"DEBUG - Survev Error Code {resp.status}: {error_details}")
                await send_update(content=f"Failed to start authorization. Server responded ({resp.status}): `{error_details}`")
                return
            data = await resp.json()

            device_code = data["deviceCode"]
            user_code = data["userCode"]
            verification_uri = data["verificationUriComplete"]
            interval = data.get("interval", 5)

            embed = discord.Embed(
                title="🔗 Link Your survev.de Account",
                description=f"1. Click the link to verify: [**Authorize survev.de account**]({verification_uri})\n"
                            f"2. Confirm code: **`{user_code}`**\n\n"
                            f"*Waiting for authorization...*",
                color=discord.Color.blue()
            )
            await send_update(embed=embed)

            token_payload = {
                "grantType": "device_code",
                "clientId": SURVEV_CLIENT_ID,
                "clientSecret": SURVEV_CLIENT_SECRET,
                "deviceCode": device_code
            }

            while True:
                await asyncio.sleep(interval)
                async with session.post(token_url, json=token_payload) as t_resp:
                    t_data = await t_resp.json()

                    if t_resp.status == 200:
                        access_token = t_data["accessToken"]

                        slug = username = None
                        try:
                            link_headers = {"Authorization": f"Bearer {access_token}"}
                            async with session.post("https://survev.de/api/external/discord_link", headers=link_headers) as link_resp:
                                if link_resp.status == 200:
                                    link_data = await link_resp.json()
                                    slug = link_data.get("slug")
                                    username = link_data.get("username")
                        except aiohttp.ClientError:
                            pass

                        save_token(discord_user_id, access_token, slug, username)
                        await send_update(content="✅ **Account Linked!** You are now entered into weekly & monthly leaderboards.")
                        break

                    error = t_data.get("error")
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        await asyncio.sleep(2)
                    else:
                        await send_update(content=f"❌ Authorization failed: `{error}`")
                        break


@bot.tree.command(name="verify", description="Link your survev.de account to join the server leaderboards!")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))


# ------------------------------------------------------------------
# 2. LEADERBOARD GENERATION ENGINE
# ------------------------------------------------------------------
async def fetch_player_matches_in_window(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int) -> list[dict]:
    """Pages through a verified player's own match history and returns the raw match objects (with guid etc.)."""
    headers = {"Authorization": f"Bearer {access_token}"}

    all_matches = []
    offset = 0
    limit = 200

    while True:
        payload = {
            "teamModeFilter": 7, # All modes
            "from": from_ms,
            "to": to_ms,
            "count": limit,
            "offset": offset
        }

        async with session.post("https://survev.de/api/external/match_history", headers=headers, json=payload) as resp:
            if resp.status != 200:
                break
            matches = await resp.json()

        # Stop if no matches are returned
        if not matches or not isinstance(matches, list):
            break

        all_matches.extend(matches)

        # Stop if we received less than the maximum request count (we reached the last page)
        if len(matches) < limit:
            break

        # Move to the next page
        offset += limit

    return all_matches


async def fetch_player_timeframe_stats(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int):
    """Aggregates a verified player's own matches within the timeframe. Used by the leaderboard."""
    all_matches = await fetch_player_matches_in_window(session, access_token, from_ms, to_ms)

    if not all_matches:
        return {"games": 0, "wins": 0, "kills": 0, "damage": 0}

    return {
        "games": len(all_matches),
        "wins": sum(1 for m in all_matches if m.get("rank") == 1),
        "kills": sum(m.get("kills", 0) for m in all_matches),
        "damage": sum(m.get("damage_dealt", 0) for m in all_matches)
    }


async def fetch_public_match_data(session: aiohttp.ClientSession, guid: str) -> list[dict] | None:
    """Public per-match scoreboard (no auth needed) — every player in that one game, including guests
    with no survev.de account at all (they still show up with a username, just slug=None)."""
    try:
        async with session.post("https://survev.de/api/match_data", json={"gameId": guid}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except aiohttp.ClientError:
        return None
    return data if isinstance(data, list) else None

async def generate_leaderboard_embed(period: str, days: int):
    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    leaderboard_data = []

    async with aiohttp.ClientSession() as session:
        for discord_id, token in users:
            stats = await fetch_player_timeframe_stats(session, token, from_ms, to_ms)
            if stats and stats["games"] > 0:
                leaderboard_data.append({
                    "discord_id": discord_id,
                    "stats": stats
                })

    # Sort by total Kills
    leaderboard_data.sort(key=lambda x: x["stats"]["kills"], reverse=True)

    embed = discord.Embed(
        title=f"🏆 Server {period} Leaderboard",
        description=f"Performance over the past **{days} days** (Sorted by Kills)",
        color=discord.Color.gold() if period == "Weekly" else discord.Color.purple()
    )
    embed.set_image(url=LEADERBOARD_BANNER_URL)

    if not leaderboard_data:
        embed.description = "No matches logged by verified members in this timeframe."
        embed.set_footer(text="Info courtesy of survev.de API :)")
        return embed

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""

    for idx, entry in enumerate(leaderboard_data[:10]): # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        leaderboard_text += (
            f"{rank} <@{entry['discord_id']}>\n"
            f"⚔️ Kills: **{stats['kills']}** | 🏆 Wins: **{stats['wins']}**\n"
            f"🎮 Games: **{stats['games']}** | 💥 Damage: **{stats['damage']:,}**\n\n"
        )

    embed.add_field(name="Top Players", value=leaderboard_text[:1024], inline=False)
    embed.set_footer(text="Data courtesy of survev.de API :)")
    return embed


def generate_leaderboard_image(period: str, days: int, leaderboard_rows: list[dict]) -> BytesIO:
    header_height = QUEUE_IMG_HEADER_HEIGHT
    row_height = QUEUE_IMG_ROW_HEIGHT
    table_top = header_height + QUEUE_IMG_PADDING
    height = table_top + max(len(leaderboard_rows), 1) * row_height + QUEUE_IMG_PADDING
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(46, "bold")
    subtitle_font = load_font(24, "bold")
    header_font = load_font(18, "bold")
    body_font = load_font(20)
    footer_font = load_font(15)

    for y in range(header_height):
        ratio = y / max(1, header_height - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 26), f"Server {period} Leaderboard", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 84), f"Performance over the past {days} days (Sorted by Kills)", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    table_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    table_x = QUEUE_IMG_PADDING
    columns = [
        table_x,
        table_x + round(table_width * 0.10),
        table_x + round(table_width * 0.42),
        table_x + round(table_width * 0.60),
        table_x + round(table_width * 0.78)
    ]
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [table_x + table_width - columns[-1]]

    header_y = table_top
    labels = ["#", "Player", "Kills", "Wins", "Damage"]
    for col_idx, label in enumerate(labels):
        label_x = columns[col_idx]
        label_width = draw.textbbox((0, 0), label, font=header_font)[2]
        if col_idx == 0:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        else:
            draw.text((label_x + (col_widths[col_idx] - label_width) / 2, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

    row_y = header_y + row_height
    for row_index, entry in enumerate(leaderboard_rows):
        if row_index % 2 == 0:
            draw.rectangle([table_x, row_y, table_x + table_width, row_y + row_height], fill=QUEUE_IMG_ROW_ALT)

        stats = entry["stats"]
        display_name = entry.get("display_name") or f"Player {entry['discord_id']}"
        row_values = [
            str(entry["rank"]),
            display_name,
            str(stats["kills"]),
            str(stats["wins"]),
            f"{stats['damage']:,}"
        ]

        for col_idx, value in enumerate(row_values):
            font = body_font
            fill = QUEUE_IMG_TEXT if col_idx != 2 else QUEUE_IMG_ACCENT
            cell_x = columns[col_idx]
            if col_idx == 0:
                draw.text((cell_x, row_y + 14), value, font=font, fill=fill)
            elif col_idx == 1:
                draw.text((cell_x, row_y + 14), value, font=font, fill=fill)
            else:
                value_width = draw.textbbox((0, 0), value, font=font)[2]
                centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                draw.text((centered_x, row_y + 14), value, font=font, fill=fill)

        row_y += row_height

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_leaderboard_image_payload(period: str, days: int):
    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    leaderboard_data = []

    async def build():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                stats = await fetch_player_timeframe_stats(session, token, from_ms, to_ms)
                if stats and stats["games"] > 0:
                    leaderboard_data.append({
                        "discord_id": discord_id,
                        "stats": stats
                    })

    async def wrapper():
        await build()
        if not leaderboard_data:
            return None, None, "No matches logged by verified members in this timeframe."

        leaderboard_data.sort(key=lambda x: x["stats"]["kills"], reverse=True)
        top_rows = []
        for idx, entry in enumerate(leaderboard_data[:10], start=1):
            top_rows.append({
                "rank": idx,
                "discord_id": entry["discord_id"],
                "stats": entry["stats"],
                "display_name": None
            })

        image_buffer = generate_leaderboard_image(period, days, top_rows)
        file = discord.File(image_buffer, filename=f"leaderboard_{period.lower()}.png")
        return f"Server {period} Leaderboard", file, None

    return wrapper()


# ------------------------------------------------------------------
# 3. NEATQUEUE INTEGRATION ENGINE
# ------------------------------------------------------------------
async def fetch_neatqueue_history(session: aiohttp.ClientSession, match_number: str | None = None, extra_params: dict | None = None):
    """Fetches history for the configured NeatQueue server, optionally limited to one game number or filtered by extra_params."""
    if not NEATQUEUE_SERVER_ID or NEATQUEUE_SERVER_ID == "YOUR_SERVER_ID_HERE":
        return None, "NeatQueue server ID is not configured. Set NEATQUEUE_SERVER_ID in the script or environment."

    url = f"{NEATQUEUE_API_BASE}/history/{NEATQUEUE_SERVER_ID}"
    params = None
    if match_number is not None:
        params = {
            "start_game_number": str(match_number),
            "end_game_number": str(match_number)
        }
    if extra_params:
        params = {**(params or {}), **extra_params}

    headers = {
        "Authorization": f"Bearer {NEATQUEUE_API_TOKEN}",
        "Content-Type": "application/json"
    }

    async with session.get(url, headers=headers, params=params) as resp:
        text = await resp.text()
        if resp.status != 200:
            error_detail = text
            try:
                error_detail_json = json.loads(text)
                error_detail = error_detail_json
            except Exception:
                pass
            if resp.status == 404:
                return None, (
                    f"NeatQueue endpoint returned 404 for URL {url}. "
                    f"Verify NEATQUEUE_API_BASE, NEATQUEUE_SERVER_ID, and match number; response detail: {error_detail}"
                )
            return None, f"NeatQueue API returned {resp.status} for URL {url}: {error_detail}"

        try:
            data = json.loads(text)
        except Exception as exc:
            return None, f"NeatQueue returned invalid JSON from {url}: {exc}"

        return data, None


async def fetch_neatqueue_matches_since(session: aiohttp.ClientSession, start_date_iso: str):
    """Fetches every match for the configured server that finished at/after start_date, oldest first."""
    data, error = await fetch_neatqueue_history(session, extra_params={
        "start_date": start_date_iso,
        "order": "asc",
        "page_size": "1000"
    })
    if error:
        return None, error
    return extract_neatqueue_entries(data), None


def extract_neatqueue_entries(history_data):
    if isinstance(history_data, list):
        return history_data
    if isinstance(history_data, dict):
        for key in ("matches", "history", "data", "entries"):
            if isinstance(history_data.get(key), list):
                return history_data[key]
        return [history_data]
    return []


def get_nested_value(entry, key):
    if not isinstance(entry, dict):
        return None
    if key in entry:
        return entry[key]
    for value in entry.values():
        if isinstance(value, dict):
            nested = get_nested_value(value, key)
            if nested is not None:
                return nested
    return None


def parse_neatqueue_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                return None
    return None


def get_match_start_ms(match):
    for key in ("start_time_ms", "start_time", "time", "timestamp"):
        value = get_nested_value(match, key)
        parsed = parse_neatqueue_time(value)
        if parsed is not None:
            return parsed
    return None


async def get_match_end_ms(match, default_start_ms: int):
    # NeatQueue edits the winner message in place once the whole series (all games) has finished, so that
    # message's actual edited_at (fetched live) is the most reliable end boundary we can get — far better
    # than guessing a fixed duration. Every candidate below is clamped to be after start_ms, since a stale
    # or replayed history entry could otherwise hand us a "winner_message" that predates the match.
    winner_message_id = get_nested_value(match, "winner_message")
    winner_channel_id = get_nested_value(match, "winner_channel")
    if winner_message_id and winner_channel_id:
        try:
            channel = bot.get_channel(int(winner_channel_id)) or await bot.fetch_channel(int(winner_channel_id))
            winner_message = await channel.fetch_message(int(winner_message_id))
            end_dt = winner_message.edited_at or winner_message.created_at
            end_ms = int(end_dt.timestamp() * 1000) + 5000
            if end_ms > default_start_ms:
                return end_ms
        except (discord.HTTPException, discord.Forbidden, discord.NotFound, TypeError, ValueError, AttributeError):
            pass

    if winner_message_id:
        try:
            end_dt = discord.utils.snowflake_time(int(winner_message_id))
            end_ms = int(end_dt.timestamp() * 1000) + 5000
            if end_ms > default_start_ms:
                return end_ms
        except (TypeError, ValueError):
            pass

    end_ms = get_nested_value(match, "end_time_ms")
    parsed_end = parse_neatqueue_time(end_ms)
    if parsed_end is not None and parsed_end > default_start_ms:
        return parsed_end

    player_timestamps = []
    for team in collect_neatqueue_teams(match):
        for player in team:
            timestamp_value = get_nested_value(player, "timestamp") or get_nested_value(player, "time")
            parsed = parse_neatqueue_time(timestamp_value)
            if parsed is not None:
                player_timestamps.append(parsed)

    if player_timestamps:
        latest = max(player_timestamps) + 5000
        if latest > default_start_ms:
            return latest

    return default_start_ms + QUEUE_MATCH_FALLBACK_DURATION_MS


NEATQUEUE_MATCH_ID_KEYS = ("match_id", "id", "matchId", "game_id", "gameId", "server_match_id", "game_number", "gameNumber")


def find_neatqueue_match(entries, match_id):
    match_id_str = str(match_id).lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in NEATQUEUE_MATCH_ID_KEYS:
            value = get_nested_value(entry, key)
            if value is not None and str(value).lower() == match_id_str:
                return entry
    return None


def get_match_game_number(match):
    for key in NEATQUEUE_MATCH_ID_KEYS:
        value = get_nested_value(match, key)
        if value is not None:
            return value
    return None


def _collect_neatqueue_player_dicts(data):
    """Recursively flattens an arbitrarily nested players/teams structure into a flat list of player dicts."""
    if isinstance(data, dict):
        if "id" in data and isinstance(data["id"], str):
            return [data]
        players = []
        for value in data.values():
            players.extend(_collect_neatqueue_player_dicts(value))
        return players
    if isinstance(data, list):
        players = []
        for item in data:
            players.extend(_collect_neatqueue_player_dicts(item))
        return players
    return []


def collect_neatqueue_teams(match):
    """Returns a list of teams (one list of player dicts per team), preserving NeatQueue's team_num order."""
    if not isinstance(match, dict):
        return []

    # NeatQueue's own schema: "teams" is a list of teams, each a list of player dicts.
    teams_field = match.get("teams")
    if isinstance(teams_field, list) and teams_field:
        return [_collect_neatqueue_player_dicts(team) for team in teams_field]

    players_field = match.get("players")
    if isinstance(players_field, list) and players_field:
        # Already grouped by team (list of lists)?
        if isinstance(players_field[0], list):
            return [_collect_neatqueue_player_dicts(team) for team in players_field]
        return [_collect_neatqueue_player_dicts(players_field)]

    return []


async def calculate_queue_match_stats(match_id: str):
    """Builds the queue's teams directly from survev.de's own match data (ground truth for who played
    and which team_id they were on), then overlays a Discord display name wherever the player's slug
    matches a /verify'd user. Anyone without a linked account just keeps their survev.de username."""
    async with aiohttp.ClientSession() as session:
        nq_data, error = await fetch_neatqueue_history(session, match_id)
        if error:
            return None, error
        if not nq_data:
            return None, "NeatQueue returned empty history data."

        entries = extract_neatqueue_entries(nq_data)
        if not entries:
            return None, "NeatQueue history contains no match entries."

        # NeatQueue returns the requested match as the first entry in the result.
        match = entries[0]

        start_ms = get_match_start_ms(match)
        if start_ms is None:
            return None, "NeatQueue match entry is missing a recognizable start time."

        end_ms = await get_match_end_ms(match, start_ms)

        teams = collect_neatqueue_teams(match)
        if not teams:
            return None, "NeatQueue match entry contains no player roster."

        # Step 1: at least one verified player is needed as an "anchor" — pull their own scoped match
        # history and use its guids to pin down exactly which survev.de match(es) this queue played.
        # This is far more reliable than any time-window guess (handles Best-of-N cleanly).
        anchor_guid_sets = []
        for team_players in teams:
            for player in team_players:
                try:
                    discord_id = int(player.get("id"))
                except (TypeError, ValueError):
                    continue
                token = get_user_token(discord_id)
                if not token:
                    continue

                own_matches = await fetch_player_matches_in_window(session, token, start_ms, end_ms)
                guids = {m.get("guid") for m in own_matches if m.get("guid")}
                if guids:
                    anchor_guid_sets.append(guids)

        if not anchor_guid_sets:
            return None, "No linked (/verify'd) player was found in this queue — need at least one to find the match."

        queue_guids = set.intersection(*anchor_guid_sets) if len(anchor_guid_sets) > 1 else anchor_guid_sets[0]
        if not queue_guids:
            # Anchors disagree entirely (e.g. inconsistent windowing) — union is safer than nothing.
            queue_guids = set.union(*anchor_guid_sets)

        # Step 2: pull the public scoreboard for every identified round and aggregate per survev.de
        # player (keyed by slug when present, else username — covers accounts with no public slug).
        players: dict[str, dict] = {}
        for guid in queue_guids:
            board = await fetch_public_match_data(session, guid)
            if not board:
                continue
            for p in board:
                username = (p.get("username") or "").strip()
                slug = p.get("slug")
                key = slug or username.lower()
                if not key:
                    continue
                agg = players.setdefault(key, {
                    "username": username, "slug": slug, "team_id": p.get("team_id"),
                    "games": 0, "wins": 0, "kills": 0, "damage": 0
                })
                agg["games"] += 1
                agg["wins"] += 1 if p.get("rank") == 1 else 0
                agg["kills"] += p.get("kills", 0)
                agg["damage"] += p.get("damage_dealt", 0)

        if not players:
            return None, "survev.de returned no player data for this queue's match(es)."

        # Step 3: group by survev.de's own team_id (ground truth — no NeatQueue roster matching needed),
        # resolving a Discord identity wherever the slug belongs to a /verify'd user.
        team_ids = sorted({p["team_id"] for p in players.values() if p["team_id"] is not None})
        team_id_index = {tid: i for i, tid in enumerate(team_ids)}
        result_teams = [[] for _ in team_ids] or [[]]

        for p in players.values():
            idx = team_id_index.get(p["team_id"], 0)
            discord_id = get_discord_id_by_slug(p["slug"]) if p["slug"] else None
            result_teams[idx].append({
                "discord_id": discord_id,
                "username": p["username"],
                "slug": p["slug"],
                "guest": discord_id is None,
                "stats": {"games": p["games"], "wins": p["wins"], "kills": p["kills"], "damage": p["damage"]}
            })

        for team in result_teams:
            team.sort(key=lambda x: x["stats"]["damage"], reverse=True)

        # Winning team = whoever won more rounds overall (every teammate shares the same round-win count).
        team_round_wins = [max((e["stats"]["wins"] for e in team), default=0) for team in result_teams]
        winning_team_index = team_round_wins.index(max(team_round_wins)) if team_round_wins and max(team_round_wins) > 0 else None

        return {"teams": result_teams, "winning_team_index": winning_team_index}, None


# ------------------------------------------------------------------
# 4. SLASH COMMANDS
# ------------------------------------------------------------------
class LeaderboardView(discord.ui.View):
    def __init__(self, initial_period: str = "Weekly"):
        super().__init__(timeout=None)
        self.initial_period = initial_period

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.primary, custom_id="leaderboard_weekly")
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._refresh(interaction, "Weekly", 7)

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id="leaderboard_monthly")
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._refresh(interaction, "Monthly", 30)

    async def _refresh(self, interaction: discord.Interaction, period: str, days: int):
        await interaction.response.defer()
        content, file, error = await build_leaderboard_image_payload(period, days)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.response.edit_message(content=content, attachments=[file], view=LeaderboardView(period))


@bot.tree.command(name="leaderboard_weekly", description="View the top players over the past 7 days.")
async def leaderboard_weekly(interaction: discord.Interaction):
    await interaction.response.defer()
    content, file, error = await build_leaderboard_image_payload("Weekly", 7)
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send(content=content, file=file, view=LeaderboardView("Weekly"))


@bot.tree.command(name="leaderboard_monthly", description="View the top players over the past 30 days.")
async def leaderboard_monthly(interaction: discord.Interaction):
    await interaction.response.defer()
    content, file, error = await build_leaderboard_image_payload("Monthly", 30)
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send(content=content, file=file, view=LeaderboardView("Monthly"))


class QueueResultView(discord.ui.View):
    """Persistent view attached to queue-result messages, so anyone missing from the roster can self-serve
    a DM verification link instead of having to know `/verify` exists."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Not showing up? Verify", style=discord.ButtonStyle.secondary, custom_id="queue_result_verify", emoji="🔗")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send("Let's link your survev.de account so your stats show up here.")
        except discord.Forbidden:
            await interaction.followup.send("❌ I can't DM you — enable DMs from server members and try again, or run `/verify` instead.", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send("❌ Something went wrong opening your DMs — try `/verify` instead.", ephemeral=True)
            return

        await interaction.followup.send("📬 Check your DMs for a verification link!", ephemeral=True)
        await run_survev_verification(interaction.user.id, lambda **kw: dm_channel.send(**kw))


queue_result_view = QueueResultView()


async def build_queue_stats_payload(match_id: str):
    """Runs the NeatQueue/survev.de cross-reference and returns either
    (content, discord.File, None) on success or (None, None, error_text) on failure."""
    match_result, error = await calculate_queue_match_stats(match_id)
    if error:
        return None, None, f"❌ {error}"

    teams = match_result["teams"]
    if not any(teams):
        return None, None, "This NeatQueue match has no player roster to show stats for."

    await resolve_queue_user_display_names(teams, bot)
    image_buffer = generate_queue_result_image(match_id, teams, match_result["winning_team_index"])
    file = discord.File(image_buffer, filename=f"queue_stats_{match_id}.png")
    return f"Queue stats for match #{match_id}", file, None


@bot.tree.command(name="queue_stats", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queue_stats(interaction: discord.Interaction, match_id: str):
    await interaction.response.defer()

    content, file, error_text = await build_queue_stats_payload(match_id)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file, view=queue_result_view)


# ------------------------------------------------------------------
# 5. AUTOMATIC NEATQUEUE RESULT DETECTION
# ------------------------------------------------------------------
@bot.tree.command(name="setup", description="Set the channel NeatQueue posts match results in, so stats get posted automatically.")
@discord.app_commands.describe(channel="The channel where the NeatQueue bot posts its match result embeds")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    set_guild_queue_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(
        f"✅ NeatQueue results in {channel.mention} will now be tracked automatically.",
        ephemeral=True
    )


def find_neatqueue_embed_match(message: discord.Message, title_patterns: tuple[re.Pattern, ...]):
    """Returns the match_id if a NeatQueue embed in this message's configured channel matches any of title_patterns."""
    if message.author.id != NEATQUEUE_BOT_ID:
        return None

    if message.guild is None or not message.embeds:
        print(f"DEBUG - NeatQueue msg {message.id}: skipped (guild={message.guild}, embeds={len(message.embeds)})")
        return None

    configured_channel_id = get_guild_queue_channel(message.guild.id)
    if configured_channel_id is None or message.channel.id != configured_channel_id:
        print(f"DEBUG - NeatQueue msg {message.id}: channel {message.channel.id} != configured {configured_channel_id} for guild {message.guild.id}")
        return None

    for embed in message.embeds:
        if not embed.title:
            continue
        for title_pattern in title_patterns:
            match = title_pattern.search(embed.title)
            if match:
                return match.group(1)

    patterns_desc = [p.pattern for p in title_patterns]
    print(f"DEBUG - NeatQueue msg {message.id}: titles {[e.title for e in message.embeds]} matched none of {patterns_desc}")
    return None


async def post_queue_result(message: discord.Message, match_id: str):
    """Marks match_id as processed for this guild and, if new, replies to message with its stats."""
    if not try_mark_match_processed(message.guild.id, match_id):
        print(f"DEBUG - Match {match_id} in guild {message.guild.id} already marked processed, skipping repost")
        return  # already posted (e.g. the panel got edited again after a revert)

    # Give the game server a moment to finish writing this match's results before querying survev.de.
    await asyncio.sleep(QUEUE_RESULT_FETCH_DELAY_SECONDS)

    content, file, error_text = await build_queue_stats_payload(match_id)
    if error_text:
        print(f"DEBUG - Match {match_id}: build_queue_stats_payload failed: {error_text}")
        await message.reply(error_text)
    else:
        print(f"DEBUG - Match {match_id}: posted queue stats successfully")
        await message.reply(content=content, file=file, view=queue_result_view)

    # Mark this guild caught up so a later restart doesn't re-post this match during backfill.
    update_guild_last_updated(message.guild.id, datetime.now(timezone.utc).isoformat())


@bot.event
async def on_message(message: discord.Message):
    if message.guild is not None and message.author.id != bot.user.id:
        # The winner announcement is sometimes already complete when first posted...
        match_id = find_neatqueue_embed_match(message, (QUEUE_WINNER_TITLE_PATTERN,))
        if match_id is not None:
            await post_queue_result(message, match_id)
    await bot.process_commands(message)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    # ...but NeatQueue also edits messages in place to fill in/finalize a result (the admin "Results for
    # Queue#" panel always works this way, and the "Winner For Queue#" announcement sometimes does too).
    # Uses the raw event (not on_message_edit) because on_message_edit only fires for messages still in
    # discord.py's message cache, and these messages are often edited long after they scroll out of cache.
    if payload.guild_id is None:
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return

    if message.author.id == bot.user.id:
        return

    match_id = find_neatqueue_embed_match(message, (QUEUE_WINNER_TITLE_PATTERN, QUEUE_PANEL_TITLE_PATTERN))
    if match_id is not None:
        await post_queue_result(message, match_id)


async def backfill_missed_queue_results():
    """On startup, catch up on any NeatQueue matches that finished while the bot was offline."""
    guild_configs = get_all_guild_settings()
    if not guild_configs:
        return

    async with aiohttp.ClientSession() as session:
        for guild_id, channel_id, last_updated in guild_configs:
            matches, error = await fetch_neatqueue_matches_since(session, last_updated)
            if error:
                print(f"DEBUG - Backfill failed for guild {guild_id}: {error}")
                continue
            if not matches:
                continue

            channel = bot.get_channel(channel_id)
            if channel is None:
                continue

            for match in matches:
                match_id = get_match_game_number(match)
                if match_id is None:
                    continue
                match_id = str(match_id)

                if not try_mark_match_processed(guild_id, match_id):
                    continue  # already posted live before the bot went down

                content, file, error_text = await build_queue_stats_payload(match_id)
                if error_text:
                    continue  # nothing worth posting (e.g. no verified players), skip silently on catch-up
                await channel.send(content=f"*(Catching up)* {content}", file=file, view=queue_result_view)

            update_guild_last_updated(guild_id, datetime.now(timezone.utc).isoformat())


# --- RUN BOT ---
bot.run(DISCORD_BOT_TOKEN)