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
        access_token TEXT NOT NULL
    )
''')
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
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await backfill_missed_queue_results()

# Helper: Save User Token
def save_token(discord_id: int, token: str):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute("INSERT OR REPLACE INTO users (discord_id, access_token) VALUES (?, ?)", (discord_id, token))

# Helper: Get All User Tokens
def get_all_users():
    with sqlite3.connect("leaderboard.db") as c:
        return c.execute("SELECT discord_id, access_token FROM users").fetchall()

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


def get_user_display_name(client: discord.Client, discord_id: int) -> str:
    user = client.get_user(discord_id)
    if user is not None:
        return user.name
    return f"Player {discord_id}"


async def resolve_queue_user_display_names(teams: list[list[dict]], client: discord.Client):
    for team in teams:
        for entry in team:
            discord_id = entry.get("discord_id")
            if discord_id is None:
                entry["display_name"] = "Unknown"
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

# Column offsets as a fraction of a team panel's width: Player, Kills, Damage, Avg Damage, Wins
QUEUE_IMG_COLUMN_RATIOS = [0.0, 0.33, 0.50, 0.70, 0.88]
QUEUE_IMG_COLUMN_LABELS = ["Player", "K", "Dmg", "Avg Dmg", "W"]


def generate_queue_result_image(match_id: str, teams: list[list[dict]], winning_team_index: int | None, client: discord.Client) -> BytesIO:
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
    badge_margin_x = 14
    badge_margin_y = 10
    draw.rectangle(
        [
            winner_bbox[0] - badge_margin_x,
            winner_bbox[1] - badge_margin_y,
            winner_bbox[2] + badge_margin_x,
            winner_bbox[3] + badge_margin_y,
        ],
        fill=QUEUE_IMG_HEADER_BG,
        outline=None,
    )
    draw.text((QUEUE_IMG_PADDING, 80), winner_text, font=subtitle_font, fill=QUEUE_IMG_WIN)

    score_font = load_font(44, "bold")
    if num_teams == 2:
        left_text = str(len(teams[0]))
        right_text = str(len(teams[1]))
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
        score_text = " / ".join(str(len(team)) for team in teams)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        section_left = winner_bbox[2] + 24
        section_right = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING
        x_pos = section_left + max(0, (section_right - section_left - (score_bbox[2] - score_bbox[0])) / 2) + 12
        y_pos = 74
        draw.text((x_pos, y_pos), score_text, font=score_font, fill=QUEUE_IMG_TEXT)

    draw.text((QUEUE_IMG_PADDING, 118), "Player stats from verified survev.de accounts for this queue", font=footer_font, fill=QUEUE_IMG_MUTED)

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING

    for team_index in range(num_teams):
        team_players = teams[team_index] if team_index < len(teams) else []
        x0 = panel_x_positions[team_index]
        is_winner = winning_team_index == team_index
        team_color = QUEUE_IMG_WIN if is_winner else (QUEUE_IMG_LOSE if winning_team_index is not None else QUEUE_IMG_MUTED)

        team_label = f"Team {team_index + 1}"
        team_label_bbox = draw.textbbox((0, 0), team_label, font=team_header_font)
        draw.text((x0, panel_top), team_label, font=team_header_font, fill=team_color)

        header_y = panel_top + QUEUE_IMG_TEAM_HEADER_HEIGHT - 24
        for col_idx, label in enumerate(QUEUE_IMG_COLUMN_LABELS):
            draw.text((x0 + columns[col_idx], header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

        if not team_players:
            draw.text((x0, rows_top), "No verified players", font=body_font, fill=QUEUE_IMG_MUTED)

        for row_index, entry in enumerate(team_players):
            row_top = rows_top + row_index * QUEUE_IMG_ROW_HEIGHT
            row_bottom = row_top + QUEUE_IMG_ROW_HEIGHT - 8
            if row_index % 2 == 0:
                draw.rectangle([x0, row_top, x0 + panel_width, row_bottom], fill=QUEUE_IMG_ROW_ALT)

            stats = entry["stats"]
            games = stats["games"]
            avg_damage = stats["damage"] / games if games else 0
            player_label = entry.get("display_name") or get_user_display_name(client, entry["discord_id"])

            row_values = [
                player_label,
                str(stats["kills"]),
                f"{stats['damage']:,}",
                f"{avg_damage:,.0f}",
                str(stats["wins"])
            ]
            for col_idx, value in enumerate(row_values):
                fill = QUEUE_IMG_ACCENT if col_idx == 2 else QUEUE_IMG_TEXT
                font = body_font_bold if col_idx in (0, 2, 3) else body_font
                draw.text((x0 + columns[col_idx], row_top + 14), value, font=font, fill=fill)

        panel_bottom = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT
        draw.rectangle([x0 - 8, panel_top - 8, x0 + panel_width + 8, panel_bottom], outline=team_color, width=2)

    footer_text = "Data courtesy of NeatQueue & survev.de APIs :)"
    draw.text((QUEUE_IMG_PADDING, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# ------------------------------------------------------------------
# 1. VERIFICATION COMMAND
# ------------------------------------------------------------------
@bot.tree.command(name="verify", description="Link your survev.de account to join the server leaderboards!")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
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
                await interaction.followup.send(f"Failed to start authorization. Server responded ({resp.status}): `{error_details}`")
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
            await interaction.followup.send(embed=embed, ephemeral=True)

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
                        save_token(interaction.user.id, access_token)
                        await interaction.followup.send("✅ **Account Linked!** You are now entered into weekly & monthly leaderboards.", ephemeral=True)
                        break
                    
                    error = t_data.get("error")
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        await asyncio.sleep(2)
                    else:
                        await interaction.followup.send(f"❌ Authorization failed: `{error}`", ephemeral=True)
                        break


# ------------------------------------------------------------------
# 2. LEADERBOARD GENERATION ENGINE
# ------------------------------------------------------------------
async def fetch_player_timeframe_stats(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int):
    """Pages through all available matches in the timeframe using offset."""
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

    if not all_matches:
        return {"games": 0, "wins": 0, "kills": 0, "damage": 0}

    return {
        "games": len(all_matches),
        "wins": sum(1 for m in all_matches if m.get("rank") == 1),
        "kills": sum(m.get("kills", 0) for m in all_matches),
        "damage": sum(m.get("damage_dealt", 0) for m in all_matches)
    }

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


def get_match_end_ms(match, default_start_ms: int):
    end_ms = get_nested_value(match, "end_time_ms")
    parsed_end = parse_neatqueue_time(end_ms)
    if parsed_end is not None:
        return parsed_end

    player_timestamps = []
    for player in match.get("players", []):
        timestamp_value = get_nested_value(player, "timestamp") or get_nested_value(player, "time")
        parsed = parse_neatqueue_time(timestamp_value)
        if parsed is not None:
            player_timestamps.append(parsed)

    if player_timestamps:
        return max(player_timestamps) + 5000
    return default_start_ms + 60000


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


NEATQUEUE_WINNER_KEYS = ("winning_team_index", "winner_team_index", "winningTeamIndex", "winner")


def get_winning_team_index(match):
    for key in NEATQUEUE_WINNER_KEYS:
        value = get_nested_value(match, key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


async def calculate_queue_match_stats(match_id: str):
    """Cross-references one NeatQueue match from server history with survev.de stats, grouped by team."""
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

        end_ms = get_match_end_ms(match, start_ms)

        teams = collect_neatqueue_teams(match)
        if not teams:
            return None, "NeatQueue match entry contains no player roster."

        result_teams = []
        for team_players in teams:
            team_results = []
            for player in team_players:
                p_id = player.get("id")
                try:
                    discord_id = int(p_id)
                except (TypeError, ValueError):
                    continue

                token = get_user_token(discord_id)
                if not token:
                    continue  # Skip unverified players

                stats = await fetch_player_timeframe_stats(session, token, start_ms, end_ms)
                if stats:
                    team_results.append({
                        "discord_id": discord_id,
                        "stats": stats
                    })

            team_results.sort(key=lambda x: x["stats"]["damage"], reverse=True)
            result_teams.append(team_results)

        return {"teams": result_teams, "winning_team_index": get_winning_team_index(match)}, None


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
        embed = await generate_leaderboard_embed(period=period, days=days)
        await interaction.message.edit(embed=embed, view=LeaderboardView(period))


@bot.tree.command(name="leaderboard_weekly", description="View the top players over the past 7 days.")
async def leaderboard_weekly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed(period="Weekly", days=7)
    await interaction.followup.send(embed=embed, view=LeaderboardView("Weekly"))


@bot.tree.command(name="leaderboard_monthly", description="View the top players over the past 30 days.")
async def leaderboard_monthly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed(period="Monthly", days=30)
    await interaction.followup.send(embed=embed, view=LeaderboardView("Monthly"))


async def build_queue_stats_payload(match_id: str):
    """Runs the NeatQueue/survev.de cross-reference and returns either
    (content, discord.File, None) on success or (None, None, error_text) on failure."""
    match_result, error = await calculate_queue_match_stats(match_id)
    if error:
        return None, None, f"❌ {error}"

    teams = match_result["teams"]
    if not any(teams):
        return None, None, "No verified players were found in this NeatQueue match, or no games were logged during the time frame."

    await resolve_queue_user_display_names(teams, bot)
    image_buffer = generate_queue_result_image(match_id, teams, match_result["winning_team_index"], bot)
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

    await interaction.followup.send(content=content, file=file)


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
        await message.reply(content=content, file=file)

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
                await channel.send(content=f"*(Catching up)* {content}", file=file)

            update_guild_last_updated(guild_id, datetime.now(timezone.utc).isoformat())


# --- RUN BOT ---
bot.run(DISCORD_BOT_TOKEN)