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
import textwrap
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

LEADERBOARD_BANNER_URL = os.getenv("LEADERBOARD_BANNER_URL", "https://media.giphy.com/media/3o6ZtpxSZbQ2zYpH0A/giphy.gif")

# --- CONFIGURATION ---

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SURVEV_CLIENT_ID = os.getenv("SURVEV_CLIENT_ID")
SURVEV_CLIENT_SECRET = os.getenv("SURVEV_CLIENT_SECRET")
NEATQUEUE_API_TOKEN = os.getenv("NEATQUEUE_API_TOKEN")
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
QUEUE_STATS_LOG_DIR = "log"
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
cursor.execute('''
    CREATE TABLE IF NOT EXISTS hall_of_fame (
        record_type TEXT PRIMARY KEY,
        value REAL NOT NULL,
        discord_id INTEGER,
        display_name TEXT,
        match_id TEXT NOT NULL,
        guild_id INTEGER NOT NULL,
        achieved_at TEXT NOT NULL,
        duration_ms INTEGER
    )
''')
# Older DBs created before duration tracking existed — add it on if missing.
try:
    cursor.execute("ALTER TABLE hall_of_fame ADD COLUMN duration_ms INTEGER")
except sqlite3.OperationalError:
    pass
conn.commit()

# NeatQueue's final results announcement, e.g. "🏆 Winner For Queue#3674 🏆" — already final when posted.
QUEUE_WINNER_TITLE_PATTERN = re.compile(r"Winner For Queue#(\d+)", re.IGNORECASE)
# NeatQueue's admin queue panel, e.g. "Results for Queue#3889" — posted at queue start with no result yet,
# then edited in place once the queue finishes. Only the edit carries a real result.
QUEUE_PANEL_TITLE_PATTERN = re.compile(r"Results for Queue#(\d+)", re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed so client.get_all_members() has data — required for real display names
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)

@bot.event
async def on_ready():
    try:
        bot.add_view(queue_result_view)
        print(f"DEBUG - add_view succeeded: is_persistent={queue_result_view.is_persistent()} children={queue_result_view.children}")
    except Exception as exc:
        print(f"DEBUG - add_view FAILED: {exc!r}")

    try:
        # Add a persistent inventory view template (buttons will be updated per message)
        template_inventory_view = InventoryPaginationView(1)
        bot.add_view(template_inventory_view)
        print(f"DEBUG - inventory view added: is_persistent={template_inventory_view.is_persistent()}")
    except Exception as exc:
        print(f"DEBUG - inventory view add_view FAILED: {exc!r}")

    try:
        # Add a persistent shop view template (buttons will be updated per message)
        template_shop_view = ShopPaginationView(1)
        bot.add_view(template_shop_view)
        print(f"DEBUG - shop view added: is_persistent={template_shop_view.is_persistent()}")
    except Exception as exc:
        print(f"DEBUG - shop view add_view FAILED: {exc!r}")

    try:
        store = bot._connection._view_store
        print(f"DEBUG - view_store synced custom_ids: {list(store._synced_message_views.keys()) if hasattr(store, '_synced_message_views') else 'n/a'}")
        print(f"DEBUG - view_store persistent listeners: {list(getattr(store, '_views', {}).keys())}")
    except Exception as exc:
        print(f"DEBUG - view_store introspection failed: {exc!r}")

    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await backfill_missing_slugs()
    await backfill_missed_queue_results()


async def log_interaction(interaction: discord.Interaction):
    # Confirmed via logs that this fires reliably for every component click, while discord.py's own
    # View/Button dispatch mysteriously never invokes our registered callbacks — so every button in the
    # bot is handled directly here instead of relying on that broken path.
    # Registered via add_listener (not @bot.event) so it can't replace app command dispatch.
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")

    def get_selected_sort_from_message(message: discord.Message | None) -> str:
        if message is None:
            return "kills"
        for row in getattr(message, "components", []):
            for component in getattr(row, "children", []):
                if getattr(component, "custom_id", None) != "leaderboard_sort":
                    continue
                for option in getattr(component, "options", []):
                    if getattr(option, "default", False):
                        return option.value
        return "kills"

    def infer_leaderboard_period_from_message(message: discord.Message | None) -> tuple[str, int]:
        if message is None or not message.embeds:
            return "Weekly", 7
        title = message.embeds[0].title or ""
        if "Monthly" in title:
            return "Monthly", 30
        if "Weekly" in title:
            return "Weekly", 7
        return "Weekly", 7

    if custom_id == "queue_result_verify":
        print(f"DEBUG - handling queue_result_verify click for {interaction.user}")
        await interaction.response.defer(ephemeral=True)
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))
    elif custom_id == "leaderboard_weekly":
        print(f"DEBUG - handling leaderboard_weekly click for {interaction.user}")
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Weekly", 7, selected_sort)
    elif custom_id == "leaderboard_monthly":
        print(f"DEBUG - handling leaderboard_monthly click for {interaction.user}")
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Monthly", 30, selected_sort)
    elif custom_id == "leaderboard_sort":
        print(f"DEBUG - handling leaderboard_sort select for {interaction.user}")
        selected_values = interaction.data.get("values") or []
        selected_sort = selected_values[0] if selected_values else "kills"
        period, days = infer_leaderboard_period_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, period, days, selected_sort)
    elif custom_id == "inventory_prev" or custom_id == "inventory_next":
        print(f"DEBUG - handling inventory pagination {custom_id} for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in inventory_pagination_state:
            target_user, access_token, rarity_filter, current_page, total_pages = inventory_pagination_state[msg_id]
            if custom_id == "inventory_prev":
                current_page = max(0, current_page - 1)
            else:
                current_page = min(total_pages - 1, current_page + 1)
            await refresh_inventory_message(interaction, target_user, access_token, rarity_filter, current_page, msg_id)
    elif custom_id == "inventory_rarity":
        print(f"DEBUG - handling inventory rarity select for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in inventory_pagination_state:
            target_user, access_token, _, _, _ = inventory_pagination_state[msg_id]
            selected_values = interaction.data.get("values") or []
            rarity_filter = selected_values[0] if selected_values else "all"
            await refresh_inventory_message(interaction, target_user, access_token, rarity_filter, 0, msg_id)
    elif custom_id == "market_prev" or custom_id == "market_next":
        print(f"DEBUG - handling market pagination {custom_id} for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in market_pagination_state:
            target_user, access_token, mode, current_page, total_pages = market_pagination_state[msg_id]
            if custom_id == "market_prev":
                current_page = max(0, current_page - 1)
            else:
                current_page = min(total_pages - 1, current_page + 1)
            await refresh_market_message(interaction, target_user, access_token, mode, current_page, msg_id)


bot.add_listener(log_interaction, "on_interaction")

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

# Helper: Get every verified user whose slug we haven't captured yet (e.g. verified before that existed)
def get_users_missing_slug():
    with sqlite3.connect("leaderboard.db") as c:
        return c.execute("SELECT discord_id, access_token FROM users WHERE slug IS NULL").fetchall()

# Helper: Fill in a previously-unknown slug/username for an already-verified user
def update_user_slug(discord_id: int, slug: str, username: str):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute("UPDATE users SET slug = ?, username = ? WHERE discord_id = ?", (slug, username, discord_id))

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


# Every single-queue stat tracked in the /hall_of_fame board, keyed by DB record_type -> display label.
HALL_OF_FAME_RECORDS = {
    "most_kills": "Most Kills in a Queue",
    "most_avg_damage": "Most Avg Damage in a Queue",
    "longest_queue": "Longest Queue Duration",
}
MIN_GAMES_FOR_HALL_OF_FAME_UPDATE = 4


def format_duration_ms(duration_ms: int) -> str:
    total_seconds = max(0, int(duration_ms)) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


# Helper: Look up the current holder of a hall of fame record, if any has been set yet.
def get_hall_of_fame_record(record_type: str) -> dict | None:
    with sqlite3.connect("leaderboard.db") as c:
        row = c.execute(
            "SELECT value, discord_id, display_name, match_id, guild_id, achieved_at, duration_ms FROM hall_of_fame WHERE record_type = ?",
            (record_type,)
        ).fetchone()
        if row is None:
            return None
        return {
            "value": row[0], "discord_id": row[1], "display_name": row[2],
            "match_id": row[3], "guild_id": row[4], "achieved_at": row[5], "duration_ms": row[6]
        }


# Helper: Overwrites a hall of fame record only if value beats the current holder (or none exists yet).
def try_set_hall_of_fame_record(
    record_type: str,
    value: float,
    discord_id: int | None,
    display_name: str,
    match_id: str,
    guild_id: int,
    duration_ms: int | None = None,
) -> bool:
    with sqlite3.connect("leaderboard.db") as c:
        existing = c.execute("SELECT value FROM hall_of_fame WHERE record_type = ?", (record_type,)).fetchone()
        if existing is not None and value <= existing[0]:
            return False
        c.execute(
            "INSERT OR REPLACE INTO hall_of_fame "
            "(record_type, value, discord_id, display_name, match_id, guild_id, achieved_at, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_type,
                value,
                discord_id,
                display_name,
                str(match_id),
                guild_id,
                datetime.now(timezone.utc).isoformat(),
                duration_ms,
            )
        )
        return True


def clear_hall_of_fame_records() -> int:
    with sqlite3.connect("leaderboard.db") as c:
        cur = c.execute("DELETE FROM hall_of_fame")
        return cur.rowcount if cur.rowcount is not None else 0


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


def write_queue_stats_command_log(
    interaction: discord.Interaction,
    match_id: str,
    match_result: dict | None,
    error_text: str | None,
):
    """Write one JSON log entry per /queue_stats command execution."""
    os.makedirs(QUEUE_STATS_LOG_DIR, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y%m%dT%H%M%S.%fZ")
    safe_match_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(match_id))
    guild_id = interaction.guild_id
    user_id = interaction.user.id if interaction.user else None
    file_name = f"queue_stats_{timestamp}_g{guild_id}_u{user_id}_m{safe_match_id}.json"
    file_path = os.path.join(QUEUE_STATS_LOG_DIR, file_name)

    payload = {
        "event": "queue_stats_command",
        "requested_at_utc": now_utc.isoformat(),
        "match_id": str(match_id),
        "guild_id": guild_id,
        "channel_id": interaction.channel_id,
        "requested_by": {
            "discord_id": user_id,
            "username": str(interaction.user) if interaction.user else None,
            "display_name": getattr(interaction.user, "display_name", None),
        },
        "status": "error" if error_text else "ok",
        "error": error_text,
        "result_summary": None,
        "individual_games": [],
    }

    if match_result:
        teams = match_result.get("teams") or []
        payload["result_summary"] = {
            "winning_team_index": match_result.get("winning_team_index"),
            "team_round_wins": match_result.get("team_round_wins"),
            "match_history": match_result.get("match_history"),
            "total_games_played": match_result.get("total_games_played"),
            "team_count": len(teams),
            "players_per_team": [len(team) for team in teams],
        }
        payload["individual_games"] = match_result.get("individual_games") or []

    with open(file_path, "w", encoding="utf-8") as log_file:
        json.dump(payload, log_file, ensure_ascii=True, indent=2)

QUEUE_FONT_PATH = resolve_queue_font_path(QUEUE_FONT_PATHS)
QUEUE_FONT_BOLD_PATH = resolve_queue_font_path(QUEUE_FONT_BOLD_PATHS) or QUEUE_FONT_PATH

LEADERBOARD_SORT_CONFIG = {
    "kills": {"label": "Kills", "emoji": "⚔️"},
    "wins": {"label": "Wins", "emoji": "🏆"},
    "games": {"label": "Games", "emoji": "🎮"},
    "damage": {"label": "Damage", "emoji": "💥"},
}


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
QUEUE_IMG_WIDTH = 1600
QUEUE_IMG_PADDING = 44
QUEUE_IMG_HEADER_HEIGHT = 210
QUEUE_IMG_TEAM_HEADER_HEIGHT = 74
QUEUE_IMG_ROW_HEIGHT = 74

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
QUEUE_IMG_COLUMN_RATIOS = [0.0, 0.48, 0.66, 0.84]
QUEUE_IMG_COLUMN_LABELS = ["Player", "Kills", "Dmg", "Avg Dmg"]
QUEUE_IMG_NAME_PADDING_RIGHT = 16  # gap kept clear before the Kills column starts


def truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Shortens text with an ellipsis so it never overflows into the next column."""
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    while text and draw.textbbox((0, 0), text + "…", font=font)[2] > max_width:
        text = text[:-1]
    return f"{text}…" if text else "…"


def generate_queue_result_image(
    match_id: str,
    teams: list[list[dict]],
    winning_team_index: int | None,
    match_history: list[bool | None] | None = None,
    team_round_wins: list[int] | None = None,
) -> BytesIO:
    num_teams = max(len(teams), 1)
    max_rows = max((len(team) for team in teams), default=0)

    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * (num_teams + 1)) // num_teams
    panel_x_positions = [QUEUE_IMG_PADDING + i * (panel_width + QUEUE_IMG_PADDING) for i in range(num_teams)]
    columns = [round(ratio * panel_width) for ratio in QUEUE_IMG_COLUMN_RATIOS]

    rows_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING + QUEUE_IMG_TEAM_HEADER_HEIGHT
    height = rows_top + max(max_rows, 1) * QUEUE_IMG_ROW_HEIGHT + QUEUE_IMG_PADDING

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(58, "bold")
    subtitle_font = load_font(32, "bold")
    team_header_font = load_font(30, "bold")
    header_font = load_font(22, "bold")
    body_font = load_font(25)
    body_font_bold = load_font(25, "bold")
    footer_font = load_font(18)

    # Top banner with subtle gradient
    for y in range(QUEUE_IMG_HEADER_HEIGHT):
        ratio = y / max(1, QUEUE_IMG_HEADER_HEIGHT - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 32), f"#{match_id}", font=title_font, fill=QUEUE_IMG_TEXT)

    if winning_team_index is not None and 0 <= winning_team_index < num_teams:
        winner_text = f"Team {winning_team_index + 1} Wins"
        winner_color = QUEUE_IMG_WIN
    else:
        winner_text = "Result unknown"
        winner_color = QUEUE_IMG_MUTED

    winner_bbox = draw.textbbox((QUEUE_IMG_PADDING, 99), winner_text, font=subtitle_font)
    draw.text((QUEUE_IMG_PADDING, 99), winner_text, font=subtitle_font, fill=winner_color)

    if match_history:
        history_count = len(match_history)
        slice_start = 0
        history_slice = match_history

        timeline_box_width = 560
        timeline_box_height = 140
        timeline_box_x = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING - timeline_box_width
        timeline_box_y = 32
        timeline_box_fill = (28, 40, 64)
        draw.rounded_rectangle(
            [timeline_box_x, timeline_box_y, timeline_box_x + timeline_box_width, timeline_box_y + timeline_box_height],
            radius=20,
            fill=timeline_box_fill,
            outline=QUEUE_IMG_MUTED,
            width=2
        )

        title_text = "MATCH TIMELINE"
        title_bbox = draw.textbbox((0, 0), title_text, font=header_font)
        title_x = timeline_box_x + (timeline_box_width - (title_bbox[2] - title_bbox[0])) / 2
        draw.text((title_x, timeline_box_y + 16), title_text, font=header_font, fill=QUEUE_IMG_TEXT)

        axis_y = timeline_box_y + 72
        axis_x0 = timeline_box_x + 28
        axis_x1 = timeline_box_x + timeline_box_width - 28
        draw.line([(axis_x0, axis_y), (axis_x1, axis_y)], fill=QUEUE_IMG_MUTED, width=2)

        spacing = 0 if history_count <= 1 else (axis_x1 - axis_x0) / (history_count - 1)

        for idx, result in enumerate(history_slice):
            x = axis_x0 + spacing * idx
            dot_color = QUEUE_IMG_MUTED if result is None else (QUEUE_IMG_WIN if result else QUEUE_IMG_LOSE)
            dot_radius = 12 if idx < history_count - 1 else 14
            dot_bbox = [x - dot_radius, axis_y - dot_radius, x + dot_radius, axis_y + dot_radius]
            draw.ellipse(dot_bbox, fill=dot_color)

            if idx == history_count - 1:
                highlight_bbox = [x - dot_radius - 4, axis_y - dot_radius - 4, x + dot_radius + 4, axis_y + dot_radius + 4]
                draw.ellipse(highlight_bbox, outline=QUEUE_IMG_ACCENT, width=4)

            label = f"G{slice_start + idx + 1}"
            label_bbox = draw.textbbox((0, 0), label, font=body_font)
            label_x = x - (label_bbox[2] - label_bbox[0]) / 2
            draw.text((label_x, axis_y + dot_radius + 8), label, font=body_font, fill=QUEUE_IMG_TEXT)

    score_font = load_font(72, "bold")
    if team_round_wins is not None and len(team_round_wins) == num_teams:
        team_scores = team_round_wins
    else:
        # Fallback for compatibility if explicit round totals are unavailable.
        team_scores = [max((entry["stats"].get("wins", 0) for entry in team if entry["stats"]), default=0) for team in teams]
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

        x_pos = (QUEUE_IMG_WIDTH - total_width) / 2
        y_pos = 91

        draw.text((x_pos, y_pos), left_text, font=score_font, fill=left_color)
        draw.text((x_pos + left_width, y_pos), separator_text, font=score_font, fill=QUEUE_IMG_TEXT)
        draw.text((x_pos + left_width + sep_width, y_pos), right_text, font=score_font, fill=right_color)
    else:
        score_text = " / ".join(str(score) for score in team_scores)
        score_bbox = draw.textbbox((0, 0), score_text, font=score_font)
        x_pos = (QUEUE_IMG_WIDTH - (score_bbox[2] - score_bbox[0])) / 2
        y_pos = 91
        draw.text((x_pos, y_pos), score_text, font=score_font, fill=QUEUE_IMG_TEXT)

    draw.text((QUEUE_IMG_PADDING, 146), "Data courtesy of NeatQueue & survev.de APIs :)", font=footer_font, fill=QUEUE_IMG_MUTED)

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

        header_y = panel_top + QUEUE_IMG_TEAM_HEADER_HEIGHT - 30
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
                fill = QUEUE_IMG_ACCENT if col_idx == 3 else QUEUE_IMG_TEXT
                font = body_font_bold if col_idx in (0, 2, 3) else body_font
                cell_x = x0 + columns[col_idx]
                if col_idx == 0:
                    max_name_width = columns[1] - columns[0] - QUEUE_IMG_NAME_PADDING_RIGHT
                    value = truncate_to_width(draw, value, font, max_name_width)
                    draw.text((cell_x, row_top + 18), value, font=font, fill=fill)
                else:
                    value_width = draw.textbbox((0, 0), value, font=font)[2]
                    centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                    draw.text((centered_x, row_top + 18), value, font=font, fill=fill)

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
async def fetch_discord_link(session: aiohttp.ClientSession, access_token: str):
    """Returns (slug, username) for a survev.de access token, or (None, None) on failure."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.post("https://survev.de/api/external/discord_link", headers=headers) as resp:
            if resp.status != 200:
                return None, None
            data = await resp.json()
            return data.get("slug"), data.get("username")
    except aiohttp.ClientError:
        return None, None


async def backfill_missing_slugs():
    """One-time-ish catch-up: fills in slug/username for users who verified before we started storing it,
    using their access token we already have — no need for them to /verify again."""
    missing = get_users_missing_slug()
    if not missing:
        return

    async with aiohttp.ClientSession() as session:
        for discord_id, access_token in missing:
            slug, username = await fetch_discord_link(session, access_token)
            if slug:
                update_user_slug(discord_id, slug, username)


async def run_survev_verification(discord_user_id: int, send_update):
    """Runs the survev.de OAuth device-code flow, calling `send_update(**kwargs)` (a discord.py-style
    send with `content=`/`embed=`) for each step. Shared by /verify and the "not showing up?" button so
    both paths (in-channel vs DM) stay in sync."""
    device_url = "https://survev.de/api/oauth/device/code"
    token_url = "https://survev.de/api/oauth/token"

    payload = {
        "clientId": SURVEV_CLIENT_ID,
        "clientSecret": SURVEV_CLIENT_SECRET,
        "scope": ["read:discord", "read:stats", "read:inventory", "read:market"]
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
                        slug, username = await fetch_discord_link(session, access_token)
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


def get_match_history_timestamp(match: dict) -> int | None:
    if not isinstance(match, dict):
        return None
    # "end_time" (e.g. "2026-08-07T15:20:37.255Z") is the real field survev.de sends; the rest are fallbacks.
    for key in ("end_time", "end_time_ms", "start_time_ms", "start_time", "time", "timestamp", "created_at", "createdAt"):
        value = match.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    # fromisoformat can't parse a trailing "Z" (pre-3.11) — normalize to an explicit offset first.
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return int(parsed.timestamp() * 1000)
                except ValueError:
                    continue
    return None


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


async def fetch_user_inventory(session: aiohttp.ClientSession, access_token: str):
    """Fetches a survev.de user's inventory with their OAuth access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with session.post("https://survev.de/api/external/inventory", headers=headers) as resp:
            if resp.status == 200:
                return await resp.json(), None

            if resp.status == 401:
                return None, "Your survev.de token is invalid or revoked. Please run /verify again."
            if resp.status == 403:
                return None, "Your survev.de token does not have inventory access. Please re-run /verify to grant read:inventory."

            text = await resp.text()
            return None, f"survev.de inventory request failed ({resp.status}): {text}"
    except aiohttp.ClientError as exc:
        return None, f"survev.de inventory request failed: {exc}"


async def fetch_user_market(session: aiohttp.ClientSession, access_token: str):
    """Fetches a survev.de user's current market/shop data with their OAuth access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with session.post("https://survev.de/api/external/market", headers=headers) as resp:
            if resp.status == 200:
                return await resp.json(), None

            if resp.status == 401:
                return None, "Your survev.de token is invalid or revoked. Please run /verify again."
            if resp.status == 403:
                return None, "Your survev.de token does not have market access. Please re-run /verify to grant read:market."

            text = await resp.text()
            return None, f"survev.de market request failed ({resp.status}): {text}"
    except aiohttp.ClientError as exc:
        return None, f"survev.de market request failed: {exc}"


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def rarity_label(rarity: int) -> str:
    return {
        0: "Stock",
        1: "Common",
        2: "Uncommon",
        3: "Rare",
        4: "Epic",
        5: "Mythic"
    }.get(rarity, f"Rarity {rarity}")


def prettify_shop_item_type(item_type: str | None) -> str:
    if not item_type:
        return "Unknown"
    return item_type.replace("_", " ").title()


# Grid card outline colors, keyed by the same rarity ints as rarity_label.
RARITY_COLORS: dict[int, tuple[int, int, int]] = {
    0: (140, 140, 140),   # Stock - grey
    1: (85, 190, 90),     # Common - green
    2: (85, 190, 90),     # Uncommon - green
    3: (110, 190, 245),   # Rare - light blue
    4: (176, 90, 220),    # Epic - purple
    5: (191, 87, 0),      # Mythic - burnt orange
}


def rarity_color(rarity: int) -> tuple[int, int, int]:
    return RARITY_COLORS.get(rarity, QUEUE_IMG_MUTED)


def group_inventory_items(items: list[dict]) -> list[dict]:
    """Collapses duplicate copies of the same skin into one entry with a count."""
    grouped: dict[tuple, dict] = {}
    for item in items:
        name = item.get("name", "Unknown Item")
        rarity = item.get("rarity", 0)
        key = (name, rarity)
        group = grouped.get(key)
        if group is None:
            group = {"name": name, "rarity": rarity, "count": 0, "value": item.get("value")}
            grouped[key] = group
        group["count"] += 1
        if group["value"] is None:
            group["value"] = item.get("value")
    return list(grouped.values())


INVENTORY_GRID_COLUMNS = 4
INVENTORY_CARD_HEIGHT = 150
INVENTORY_CARD_GAP = 24
INVENTORY_ITEMS_PER_PAGE = 12
INVENTORY_RARITY_FILTERS: dict[str, str] = {
    "all": "🔄️ All Rarities",
    "0": "Stock",
    "1": "Common",
    "2": "Uncommon",
    "3": "Rare",
    "4": "Epic",
    "5": "Mythic",
}


def normalize_inventory_rarity_filter(rarity_filter: str | None) -> str:
    key = (rarity_filter or "all").strip().lower()
    return key if key in INVENTORY_RARITY_FILTERS else "all"


def build_inventory_rarity_counts(grouped_items: list[dict]) -> dict[str, int]:
    counts = {key: 0 for key in INVENTORY_RARITY_FILTERS}
    counts["all"] = len(grouped_items)
    for entry in grouped_items:
        rarity_key = str(entry.get("rarity", ""))
        if rarity_key in counts:
            counts[rarity_key] += 1
    return counts


def generate_inventory_image(username: str, items: list[dict], page: int = 0, rarity_filter: str = "all") -> BytesIO:
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)

    grouped_items = group_inventory_items(items)
    if rarity_key != "all":
        rarity_value = int(rarity_key)
        grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
    grouped_items.sort(key=lambda entry: (entry["rarity"], entry["value"] or 0), reverse=True)
    
    start_idx = page * INVENTORY_ITEMS_PER_PAGE
    end_idx = start_idx + INVENTORY_ITEMS_PER_PAGE
    shown_items = grouped_items[start_idx:end_idx]
    total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))

    header_height = QUEUE_IMG_HEADER_HEIGHT
    grid_top = header_height + QUEUE_IMG_PADDING
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - INVENTORY_CARD_GAP * (INVENTORY_GRID_COLUMNS - 1)) // INVENTORY_GRID_COLUMNS
    rows = max(1, -(-len(shown_items) // INVENTORY_GRID_COLUMNS))
    height = grid_top + rows * (INVENTORY_CARD_HEIGHT + INVENTORY_CARD_GAP) + QUEUE_IMG_PADDING + 60

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(22, "bold")
    name_font = load_font(21, "bold")
    count_font = load_font(18, "bold")
    value_font = load_font(20, "bold")
    footer_font = load_font(15)

    for y in range(header_height):
        ratio = y / max(1, header_height - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 26), "{username}'s Inventory", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text(
        (QUEUE_IMG_PADDING, 86),
        f"Page {page + 1}/{total_pages} ({len(shown_items)} of {len(grouped_items)} unique skins)",
        font=subtitle_font,
        fill=QUEUE_IMG_MUTED
    )

    if not shown_items:
        empty_text = "No items in this rarity."
        empty_font = load_font(30, "bold")
        empty_bbox = draw.textbbox((0, 0), empty_text, font=empty_font)
        empty_x = (QUEUE_IMG_WIDTH - (empty_bbox[2] - empty_bbox[0])) / 2
        empty_y = grid_top + (INVENTORY_CARD_HEIGHT / 2)
        draw.text((empty_x, empty_y), empty_text, font=empty_font, fill=QUEUE_IMG_MUTED)

    for idx, entry in enumerate(shown_items):
        col = idx % INVENTORY_GRID_COLUMNS
        row = idx // INVENTORY_GRID_COLUMNS
        x0 = QUEUE_IMG_PADDING + col * (card_width + INVENTORY_CARD_GAP)
        y0 = grid_top + row * (INVENTORY_CARD_HEIGHT + INVENTORY_CARD_GAP)
        x1 = x0 + card_width
        y1 = y0 + INVENTORY_CARD_HEIGHT

        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=16, fill=QUEUE_IMG_ROW_ALT, outline=rarity_color(entry["rarity"]), width=4
        )

        name_text = truncate_to_width(draw, entry["name"], name_font, card_width - 32)
        draw.text((x0 + 16, y0 + 18), name_text, font=name_font, fill=QUEUE_IMG_TEXT)

        if entry["count"] > 1:
            draw.text((x0 + 16, y0 + 52), f"x{entry['count']}", font=count_font, fill=QUEUE_IMG_MUTED)

        value = entry["value"]
        value_text = f"{value:,} 💰" if isinstance(value, (int, float)) and value else "Stock"
        draw.text((x0 + 16, y1 - 42), value_text, font=value_font, fill=QUEUE_IMG_ACCENT)

    page_text = f"Page {page + 1} of {total_pages}"
    page_width = draw.textbbox((0, 0), page_text, font=footer_font)[2]
    page_x = (QUEUE_IMG_WIDTH - page_width) / 2
    draw.text((page_x, height - 52), page_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


SHOP_GRID_COLUMNS = 2
SHOP_CARD_HEIGHT = 160
SHOP_CARD_GAP = 24
SHOP_ITEMS_PER_PAGE = 6


def generate_shop_image(username: str, market_data: dict, mode: str = "all", page: int = 0) -> BytesIO:
    balance = market_data.get("balance", 0)
    offers = market_data.get("offers", [])

    # Filter by mode
    if mode == "daily":
        shown_offers = [offer for offer in offers if offer.get("slot") in (0, 1)]
        mode_label = "Daily Offers"
    elif mode == "weekly":
        shown_offers = [offer for offer in offers if offer.get("slot") in (2, 3)]
        mode_label = "Weekly Offers"
    else:
        shown_offers = offers[:]
        mode_label = "All Offers"

    shown_offers = sorted(shown_offers, key=lambda o: o.get("slot", 0))
    total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
    
    # Clamp page
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * SHOP_ITEMS_PER_PAGE
    end_idx = start_idx + SHOP_ITEMS_PER_PAGE
    page_offers = shown_offers[start_idx:end_idx]

    header_height = QUEUE_IMG_HEADER_HEIGHT
    grid_top = header_height + QUEUE_IMG_PADDING
    grid_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    card_width = (grid_width - SHOP_CARD_GAP * (SHOP_GRID_COLUMNS - 1)) // SHOP_GRID_COLUMNS
    rows = max(1, -(-len(page_offers) // SHOP_GRID_COLUMNS))
    height = grid_top + rows * (SHOP_CARD_HEIGHT + SHOP_CARD_GAP) + QUEUE_IMG_PADDING + 60

    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(22, "bold")
    item_font = load_font(18, "bold")
    price_font = load_font(20, "bold")
    status_font = load_font(16)
    footer_font = load_font(15)

    # Gradient header
    for y in range(header_height):
        ratio = y / max(1, header_height - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 26), "survev.de Shop", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text(
        (QUEUE_IMG_PADDING, 86),
        f"{username}'s {mode_label} — Page {page + 1}/{total_pages} ({len(page_offers)} of {len(shown_offers)} offers)",
        font=subtitle_font,
        fill=QUEUE_IMG_MUTED
    )

    balance_text = f"Balance: {balance:,} <:goldenfries:1535978920481136700>"
    draw.text((QUEUE_IMG_PADDING, 130), balance_text, font=status_font, fill=QUEUE_IMG_ACCENT)

    # Render cards
    for idx, offer in enumerate(page_offers):
        col = idx % SHOP_GRID_COLUMNS
        row = idx // SHOP_GRID_COLUMNS
        x0 = QUEUE_IMG_PADDING + col * (card_width + SHOP_CARD_GAP)
        y0 = grid_top + row * (SHOP_CARD_HEIGHT + SHOP_CARD_GAP)
        x1 = x0 + card_width
        y1 = y0 + SHOP_CARD_HEIGHT

        # Card background and border - color by status
        purchased = offer.get("purchased", False)
        card_color = (60, 40, 40) if purchased else (40, 60, 40)
        border_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        
        draw.rounded_rectangle(
            [x0, y0, x1, y1], radius=16, fill=card_color, outline=border_color, width=3
        )

        # Slot info
        slot = offer.get("slot", "?")
        slot_text = f"Slot {slot}"
        draw.text((x0 + 12, y0 + 12), slot_text, font=status_font, fill=QUEUE_IMG_MUTED)

        # Items
        item_types = offer.get("items", [])
        item_label = ", ".join(prettify_shop_item_type(item.get("type")) for item in item_types[:2])
        if len(item_types) > 2:
            item_label += ", …"
        item_label = truncate_to_width(draw, item_label, item_font, card_width - 32)
        draw.text((x0 + 12, y0 + 40), item_label, font=item_font, fill=QUEUE_IMG_TEXT)

        # Price
        price = offer.get("price")
        price_text = f"{price:,} 💰" if price is not None else "?"
        draw.text((x0 + 12, y0 + 75), price_text, font=price_font, fill=QUEUE_IMG_ACCENT)

        # Status
        status_text = "✓ Bought" if purchased else "◯ Available"
        status_color = QUEUE_IMG_LOSE if purchased else QUEUE_IMG_WIN
        draw.text((x0 + 12, y1 - 30), status_text, font=status_font, fill=status_color)

    # Page indicator
    page_text = f"Page {page + 1} of {total_pages}"
    page_width = draw.textbbox((0, 0), page_text, font=footer_font)[2]
    page_x = (QUEUE_IMG_WIDTH - page_width) / 2
    draw.text((page_x, height - 52), page_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def generate_goldenfries_image(username: str, balance: int) -> BytesIO:
    header_height = QUEUE_IMG_HEADER_HEIGHT
    height = header_height + QUEUE_IMG_PADDING * 3 + 220
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(44, "bold")
    subtitle_font = load_font(24, "bold")
    counter_font = load_font(96, "bold")
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

    draw.text((QUEUE_IMG_PADDING, 26), "survev.de Golden Fries", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 86), f"{username}'s balance", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    balance_text = f"{balance:,}"
    balance_width = draw.textbbox((0, 0), balance_text, font=counter_font)[2]
    balance_x = (QUEUE_IMG_WIDTH - balance_width) / 2
    balance_y = header_height + QUEUE_IMG_PADDING
    draw.text((balance_x, balance_y), balance_text, font=counter_font, fill=QUEUE_IMG_ACCENT)

    label_text = "Golden Fries"
    label_width = draw.textbbox((0, 0), label_text, font=body_font)[2]
    label_x = (QUEUE_IMG_WIDTH - label_width) / 2
    draw.text((label_x, balance_y + 110), label_text, font=body_font, fill=QUEUE_IMG_TEXT)

    description = "idk what to put here but this is a placeholder for now"
    desc_width = draw.textbbox((0, 0), description, font=body_font)[2]
    desc_x = (QUEUE_IMG_WIDTH - desc_width) / 2
    draw.text((desc_x, balance_y + 150), description, font=body_font, fill=QUEUE_IMG_MUTED)

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_inventory_image_payload(target_user: discord.User, access_token: str, page: int = 0, rarity_filter: str = "all"):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            inventory, error = await fetch_user_inventory(session, access_token)
            if error:
                return None, None, error, None, None, None

            if not inventory:
                return None, None, "survev.de returned an empty inventory response.", None, None, None

            username = inventory.get("username") or str(target_user)
            items = inventory.get("items", [])
            rarity_key = normalize_inventory_rarity_filter(rarity_filter)
            grouped_items = group_inventory_items(items)
            rarity_counts = build_inventory_rarity_counts(grouped_items)
            if rarity_key != "all":
                rarity_value = int(rarity_key)
                grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
            total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
            image_buffer = generate_inventory_image(username, items, page, rarity_key)
            filename = f"inventory_{target_user.id}_p{page}.png"
            file = discord.File(image_buffer, filename=filename)
            return f"{username}'s survev.de inventory", file, None, total_pages, filename, rarity_counts

    return wrapper()


def build_shop_image_payload(target_user: discord.User, access_token: str, mode: str = "all", page: int = 0):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            market_data, error = await fetch_user_market(session, access_token)
            if error:
                return None, None, None, error, None

            if not market_data:
                return None, None, None, "survev.de returned an empty market response.", None

            username = market_data.get("username") or str(target_user)
            offers = market_data.get("offers", [])
            
            # Filter by mode
            if mode == "daily":
                shown_offers = [o for o in offers if o.get("slot") in (0, 1)]
            elif mode == "weekly":
                shown_offers = [o for o in offers if o.get("slot") in (2, 3)]
            else:
                shown_offers = offers
            
            total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
            
            # Clamp page
            clamped_page = max(0, min(page, total_pages - 1))
            
            image_buffer = generate_shop_image(username, market_data, mode, clamped_page)
            filename = f"shop_{target_user.id}_{mode}_p{clamped_page}.png"
            file = discord.File(image_buffer, filename=filename)
            
            mode_label = "Daily" if mode == "daily" else "Weekly" if mode == "weekly" else "All"
            content = f"{username}'s {mode_label} Offers"
            
            return content, file, filename, None, total_pages

    return wrapper()


def build_goldenfries_embed_payload(target_user: discord.User, access_token: str):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            market_data, error = await fetch_user_market(session, access_token)
            if error:
                return None, None, error

            if not market_data:
                return None, None, "survev.de returned an empty market response."

            username = market_data.get("username") or str(target_user)
            balance = market_data.get("balance", 0)
            return username, int(balance), None

    return wrapper()


def compute_inventory_worth(items: list[dict]) -> int:
    total_worth = 0
    for item in items:
        value = item.get("value")
        if isinstance(value, (int, float)):
            total_worth += int(value)
            continue
        price_paid = item.get("pricePaid")
        if isinstance(price_paid, (int, float)):
            total_worth += int(price_paid)
    return total_worth


def generate_compare_image(
    left_name: str,
    left_stats: dict | None,
    left_worth: int | None,
    right_name: str,
    right_stats: dict | None,
    right_worth: int | None,
) -> BytesIO:
    height = 820
    image = Image.new("RGB", (QUEUE_IMG_WIDTH, height), QUEUE_IMG_BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(48, "bold")
    subtitle_font = load_font(24, "bold")
    header_font = load_font(22, "bold")
    value_font = load_font(32, "bold")
    body_font = load_font(22)
    footer_font = load_font(16)

    for y in range(QUEUE_IMG_HEADER_HEIGHT):
        ratio = y / max(1, QUEUE_IMG_HEADER_HEIGHT - 1)
        gradient_color = (
            QUEUE_IMG_HEADER_BG[0] + int((QUEUE_IMG_HEADER_GRADIENT_END[0] - QUEUE_IMG_HEADER_BG[0]) * ratio),
            QUEUE_IMG_HEADER_BG[1] + int((QUEUE_IMG_HEADER_GRADIENT_END[1] - QUEUE_IMG_HEADER_BG[1]) * ratio),
            QUEUE_IMG_HEADER_BG[2] + int((QUEUE_IMG_HEADER_GRADIENT_END[2] - QUEUE_IMG_HEADER_BG[2]) * ratio),
        )
        draw.line([(0, y), (QUEUE_IMG_WIDTH, y)], fill=gradient_color)

    draw.text((QUEUE_IMG_PADDING, 28), "survev.de Compare (wip broken asf)", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 92), "All-time verified stats side-by-side", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    panel_top = QUEUE_IMG_HEADER_HEIGHT + QUEUE_IMG_PADDING
    panel_height = height - panel_top - QUEUE_IMG_PADDING
    panel_width = (QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 3) // 2
    left_x = QUEUE_IMG_PADDING
    right_x = QUEUE_IMG_PADDING * 2 + panel_width

    draw.rounded_rectangle([left_x, panel_top, left_x + panel_width, panel_top + panel_height], radius=24, fill=(18, 68, 38), outline=QUEUE_IMG_WIN, width=4)
    draw.rounded_rectangle([right_x, panel_top, right_x + panel_width, panel_top + panel_height], radius=24, fill=(68, 18, 18), outline=QUEUE_IMG_LOSE, width=4)

    draw.text((left_x + 28, panel_top + 24), left_name, font=header_font, fill=QUEUE_IMG_TEXT)
    draw.text((right_x + 28, panel_top + 24), right_name, font=header_font, fill=QUEUE_IMG_TEXT)

    left_status = "Verified" if left_stats is not None else "Not verified yet"
    right_status = "Verified" if right_stats is not None else "Not verified yet"
    draw.text((left_x + 28, panel_top + 64), left_status, font=body_font, fill=QUEUE_IMG_TEXT)
    draw.text((right_x + 28, panel_top + 64), right_status, font=body_font, fill=QUEUE_IMG_TEXT)

    label_x = left_x + 28
    value_x = left_x + 300
    right_value_x = right_x + 300
    row_top = panel_top + 140
    row_spacing = 90
    labels = ["All-Time Wins", "K/D Ratio", "Total Damage", "Inventory Worth"]

    for idx, label in enumerate(labels):
        y = row_top + idx * row_spacing
        draw.text((label_x, y), label, font=body_font, fill=QUEUE_IMG_MUTED)

        if left_stats is None:
            if idx == 0:
                left_value = "-"
            elif idx == 1:
                left_value = "-"
            elif idx == 2:
                left_value = "-"
            else:
                left_value = "-"
        else:
            if idx == 0:
                left_value = str(left_stats["wins"])
            elif idx == 1:
                left_value = f"{left_stats['kills'] / max(1, left_stats['games']):.2f}"
            elif idx == 2:
                left_value = f"{left_stats['damage']:,}"
            else:
                left_value = f"{left_worth:,} 💰"

        if right_stats is None:
            if idx == 0:
                right_value = "-"
            elif idx == 1:
                right_value = "-"
            elif idx == 2:
                right_value = "-"
            else:
                right_value = "-"
        else:
            if idx == 0:
                right_value = str(right_stats["wins"])
            elif idx == 1:
                right_value = f"{right_stats['kills'] / max(1, right_stats['games']):.2f}"
            elif idx == 2:
                right_value = f"{right_stats['damage']:,}"
            else:
                right_value = f"{right_worth:,} 💰"

        left_fill = QUEUE_IMG_WIN if left_stats is not None else QUEUE_IMG_MUTED
        right_fill = QUEUE_IMG_LOSE if right_stats is not None else QUEUE_IMG_MUTED
        draw.text((value_x, y), left_value, font=value_font, fill=left_fill)
        draw.text((right_value_x, y), right_value, font=value_font, fill=right_fill)

    if left_stats is None:
        message = "Not verified yet"
        msg_bbox = draw.textbbox((0, 0), message, font=value_font)
        draw.text(
            (left_x + (panel_width - (msg_bbox[2] - msg_bbox[0])) / 2, row_top + 4 * row_spacing),
            message,
            font=value_font,
            fill=QUEUE_IMG_MUTED
        )
    if right_stats is None:
        message = "Not verified yet"
        msg_bbox = draw.textbbox((0, 0), message, font=value_font)
        draw.text(
            (right_x + (panel_width - (msg_bbox[2] - msg_bbox[0])) / 2, row_top + 4 * row_spacing),
            message,
            font=value_font,
            fill=QUEUE_IMG_MUTED
        )

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_compare_payload(member_a: discord.User, member_b: discord.User):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            token_a = get_user_token(member_a.id)
            token_b = get_user_token(member_b.id)

            left_stats = None
            left_worth = 0
            if token_a:
                left_stats = await fetch_player_timeframe_stats(session, token_a, 0, now_ms)
                inventory_a, _ = await fetch_user_inventory(session, token_a)
                if inventory_a and isinstance(inventory_a, dict):
                    left_worth = compute_inventory_worth(inventory_a.get("items", []))

            right_stats = None
            right_worth = 0
            if token_b:
                right_stats = await fetch_player_timeframe_stats(session, token_b, 0, now_ms)
                inventory_b, _ = await fetch_user_inventory(session, token_b)
                if inventory_b and isinstance(inventory_b, dict):
                    right_worth = compute_inventory_worth(inventory_b.get("items", []))

            image_buffer = generate_compare_image(
                member_a.name,
                left_stats,
                left_worth if left_stats is not None else None,
                member_b.name,
                right_stats,
                right_worth if right_stats is not None else None,
            )
            file = discord.File(image_buffer, filename=f"compare_{member_a.id}_{member_b.id}.png")
            return f"Compare {member_a.name} vs {member_b.name}", file, None

    return wrapper()


def generate_leaderboard_fries_image(leaderboard_rows: list[dict]) -> BytesIO:
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

    draw.text((QUEUE_IMG_PADDING, 26), "Server Golden Fries Leaderboard", font=title_font, fill=QUEUE_IMG_TEXT)
    draw.text((QUEUE_IMG_PADDING, 84), "Top verified users by their survev.de Golden Fries balance.", font=subtitle_font, fill=QUEUE_IMG_MUTED)

    table_width = QUEUE_IMG_WIDTH - QUEUE_IMG_PADDING * 2
    table_x = QUEUE_IMG_PADDING
    columns = [
        table_x,
        table_x + round(table_width * 0.10),
        table_x + round(table_width * 0.55),
        table_x + round(table_width * 0.80)
    ]
    col_widths = [columns[i + 1] - columns[i] for i in range(len(columns) - 1)] + [table_x + table_width - columns[-1]]

    header_y = table_top
    labels = ["#", "Player", "Balance", "Status"]
    for col_idx, label in enumerate(labels):
        label_x = columns[col_idx]
        label_width = draw.textbbox((0, 0), label, font=header_font)[2]
        if col_idx == 0:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        elif col_idx == 1:
            draw.text((label_x, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)
        else:
            draw.text((label_x + (col_widths[col_idx] - label_width) / 2, header_y), label, font=header_font, fill=QUEUE_IMG_TEXT)

    row_y = header_y + row_height
    for row_index, entry in enumerate(leaderboard_rows):
        if row_index % 2 == 0:
            draw.rectangle([table_x, row_y, table_x + table_width, row_y + row_height], fill=QUEUE_IMG_ROW_ALT)

        balance = entry["balance"]
        display_name = entry.get("display_name") or f"Player {entry['discord_id']}"
        row_values = [
            str(entry["rank"]),
            display_name,
            f"{balance:,}",
            "Verified"
        ]

        for col_idx, value in enumerate(row_values):
            font = body_font
            fill = QUEUE_IMG_ACCENT if col_idx == 2 else QUEUE_IMG_TEXT
            cell_x = columns[col_idx]
            if col_idx == 0:
                draw.text((cell_x, row_y + 18), value, font=font, fill=fill)
            elif col_idx == 1:
                draw.text((cell_x, row_y + 18), value, font=font, fill=fill)
            else:
                value_width = draw.textbbox((0, 0), value, font=font)[2]
                centered_x = cell_x + (col_widths[col_idx] - value_width) / 2
                draw.text((centered_x, row_y + 18), value, font=font, fill=fill)

        row_y += row_height

    footer_text = "Data courtesy of survev.de API :)"
    footer_width = draw.textbbox((0, 0), footer_text, font=footer_font)[2]
    footer_x = (QUEUE_IMG_WIDTH - footer_width) / 2
    draw.text((footer_x, height - QUEUE_IMG_PADDING + 8), footer_text, font=footer_font, fill=QUEUE_IMG_MUTED)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def build_leaderboard_fries_image_payload():
    users = get_all_users()
    if not users:
        return None, None, "No verified users found."

    leaderboard_data = []
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                market_data, error = await fetch_user_market(session, token)
                if error:
                    continue

                if not market_data:
                    continue

                balance = market_data.get("balance")
                if balance is None:
                    continue

                leaderboard_data.append({
                    "discord_id": discord_id,
                    "balance": balance,
                    "display_name": None
                })

        if not leaderboard_data:
            return None, None, "No Golden Fries balances could be retrieved from verified users."

        leaderboard_data.sort(key=lambda x: x["balance"], reverse=True)
        top_rows = []
        for idx, entry in enumerate(leaderboard_data[:10], start=1):
            top_rows.append({
                "rank": idx,
                "discord_id": entry["discord_id"],
                "balance": entry["balance"],
                "display_name": None
            })

        image_buffer = generate_leaderboard_fries_image(top_rows)
        file = discord.File(image_buffer, filename="leaderboard_fries.png")
        return "Server Golden Fries Leaderboard", file, None

    return wrapper()


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


async def generate_leaderboard_embed(period: str, days: int, sort_by: str = "kills"):
    sort_key = sort_by if sort_by in LEADERBOARD_SORT_CONFIG else "kills"
    sort_label = LEADERBOARD_SORT_CONFIG[sort_key]["label"]
    sort_emoji = LEADERBOARD_SORT_CONFIG[sort_key]["emoji"]

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

    leaderboard_data.sort(key=lambda x: x["stats"][sort_key], reverse=True)

    embed = discord.Embed(
        title=f"🏆 Server {period} Leaderboard",
        description=f"Performance over the past **{days} days** (Sorted by {sort_label})",
        color=discord.Color.gold() if period == "Weekly" else discord.Color.purple()
    )
    embed.set_image(url=LEADERBOARD_BANNER_URL)

    if not leaderboard_data:
        embed.description = "No matches logged by verified members in this timeframe."
        embed.set_footer(text="Data courtesy of survev.de API :)")
        return embed

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""

    for idx, entry in enumerate(leaderboard_data[:10]): # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        stat_value = f"{stats[sort_key]:,}" if sort_key == "damage" else str(stats[sort_key])
        leaderboard_text += f"{rank} <@{entry['discord_id']}>\n{sort_emoji} {sort_label}: **{stat_value}**\n\n"

    embed.add_field(name=f"Top Players by {sort_label}", value=leaderboard_text[:1024], inline=False)
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


def build_leaderboard_fries_embed():
    users = get_all_users()
    if not users:
        return None, "No verified users found."

    leaderboard_data = []
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                market_data, error = await fetch_user_market(session, token)
                if error or not market_data:
                    continue

                balance = market_data.get("balance")
                if balance is None:
                    continue

                leaderboard_data.append({
                    "discord_id": discord_id,
                    "balance": balance
                })

        if not leaderboard_data:
            return None, "No Golden Fries balances could be retrieved from verified users."

        leaderboard_data.sort(key=lambda x: x["balance"], reverse=True)
        embed = discord.Embed(
            title="<:goldenfries:1535978920481136700> Server Golden Fries Leaderboard",
            description="Top verified users by their survev.de Golden Fries balance.",
            color=discord.Color.gold()
        )

        rank_emojis = ["🥇", "🥈", "🥉"]
        leaderboard_text = ""
        for idx, entry in enumerate(leaderboard_data[:10]):
            rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
            leaderboard_text += (
                f"{rank} <@{entry['discord_id']}>\n"
                f"🍟 Balance: **{entry['balance']:,}**\n\n"
            )

        embed.add_field(name="Top Golden Fries", value=leaderboard_text[:1024], inline=False)
        embed.set_footer(text="Data courtesy of survev.de API :)")
        return embed, None

    return wrapper()


# ------------------------------------------------------------------
# 3. NEATQUEUE INTEGRATION ENGINE
# ------------------------------------------------------------------
async def fetch_neatqueue_history(session: aiohttp.ClientSession, guild_id: int, match_number: str | None = None, extra_params: dict | None = None):
    """Fetches history for the given Discord guild's NeatQueue server (NeatQueue's server_id IS the
    guild id), optionally limited to one game number or filtered by extra_params."""
    if not guild_id:
        return None, "No Discord server ID given to look up NeatQueue history for."

    url = f"{NEATQUEUE_API_BASE}/history/{guild_id}"
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
                    f"Verify this server has NeatQueue set up and the match number is correct; response detail: {error_detail}"
                )
            return None, f"NeatQueue API returned {resp.status} for URL {url}: {error_detail}"

        try:
            data = json.loads(text)
        except Exception as exc:
            return None, f"NeatQueue returned invalid JSON from {url}: {exc}"

        return data, None


async def fetch_neatqueue_matches_since(session: aiohttp.ClientSession, guild_id: int, start_date_iso: str):
    """Fetches every match for the given guild's NeatQueue server that finished at/after start_date, oldest first."""
    data, error = await fetch_neatqueue_history(session, guild_id, extra_params={
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


async def find_queue_panel_message(guild_id: int, match_id: str):
    """Searches the guild's configured results channel for the (possibly still in-progress) NeatQueue
    panel/winner message for this match number — used when NeatQueue's history API has no entry yet
    because the queue hasn't finished."""
    channel_id = get_guild_queue_channel(guild_id)
    if channel_id is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        return None

    pattern = re.compile(rf"Queue#{re.escape(str(match_id))}\b")
    try:
        async for message in channel.history(limit=200):
            if message.author.id != NEATQUEUE_BOT_ID:
                continue
            for embed in message.embeds:
                if embed.title and pattern.search(embed.title):
                    return message
    except discord.HTTPException:
        return None
    return None


def parse_teams_from_panel_embed(embed: discord.Embed) -> list[list[dict]]:
    """Extracts team rosters (as Discord IDs) from a NeatQueue panel/winner embed's "Team N" fields,
    which list each player as a real @mention even before the queue has a result."""
    teams = []
    for field in embed.fields:
        if not field.name or not field.name.lower().startswith("team"):
            continue
        ids = re.findall(r"<@!?(\d+)>", field.value or "")
        teams.append([{"id": pid} for pid in ids])
    return teams


async def calculate_queue_match_stats(match_id: str, guild_id: int):
    """Builds the queue's teams directly from survev.de's own match data (ground truth for who played
    and which team_id they were on), then overlays a Discord display name wherever the player's slug
    matches a /verify'd user. Anyone without a linked account just keeps their survev.de username."""
    async with aiohttp.ClientSession() as session:
        nq_data, error = await fetch_neatqueue_history(session, guild_id, match_id)
        entries = extract_neatqueue_entries(nq_data) if nq_data else []
        match = entries[0] if entries else None

        # NeatQueue creates the history record early (after the first game) and does NOT keep updating
        # it as a Best-of-N series continues — only "winner" gets set once the whole series concludes.
        # So an entry existing isn't enough; only trust it once it actually has a decided winner.
        is_finished = match is not None and get_nested_value(match, "winner") is not None

        if is_finished:
            # NeatQueue returns the requested match as the first entry in the result.

            start_ms = get_match_start_ms(match)
            if start_ms is None:
                return None, "NeatQueue match entry is missing a recognizable start time."

            end_ms = await get_match_end_ms(match, start_ms)

            teams = collect_neatqueue_teams(match)
            if not teams:
                return None, "NeatQueue match entry contains no player roster."
        else:
            # No finished-match history yet — the queue is probably still in progress. Fall back to
            # NeatQueue's own panel/winner Discord message: its post time is the start, and "now" is
            # the end, so /queue_stats still works mid-queue instead of erroring out.
            panel_message = await find_queue_panel_message(guild_id, match_id)
            if panel_message is None:
                return None, error or "NeatQueue history contains no match entries."

            teams = None
            for embed in panel_message.embeds:
                parsed = parse_teams_from_panel_embed(embed)
                if parsed:
                    teams = parsed
                    break
            if not teams:
                return None, "Couldn't read the player roster from NeatQueue's message for this queue."

            start_ms = int(panel_message.created_at.timestamp() * 1000)
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Remember NeatQueue's own team ordering (by discord_id) so our survev.de-team_id-based grouping
        # can be displayed in the same left/right order NeatQueue itself uses.
        neatqueue_team_index_by_discord_id: dict[int, int] = {}
        for i, team_players in enumerate(teams):
            for player in team_players:
                try:
                    neatqueue_team_index_by_discord_id[int(player.get("id"))] = i
                except (TypeError, ValueError):
                    continue

        # Step 1: at least one verified player is needed as an "anchor" — pull their own scoped match
        # history and use its guids to pin down exactly which survev.de match(es) this queue played.
        # This is far more reliable than any time-window guess (handles Best-of-N cleanly).
        anchor_guid_sets = []
        anchor_matches_by_discord: dict[int, list[dict]] = {}
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
                    anchor_matches_by_discord[discord_id] = own_matches

        if not anchor_guid_sets:
            return None, "No linked (/verify'd) player was found in this queue — need at least one to find the match."

        queue_guids = set.intersection(*anchor_guid_sets) if len(anchor_guid_sets) > 1 else anchor_guid_sets[0]
        if not queue_guids:
            # Anchors disagree entirely (e.g. inconsistent windowing) — union is safer than nothing.
            queue_guids = set.union(*anchor_guid_sets)

        ordered_queue_guids: list[str] = []
        seen_guids: set[str] = set()
        if anchor_matches_by_discord:
            guid_timestamps: dict[str, int] = {}
            for matches in anchor_matches_by_discord.values():
                for match in matches:
                    guid = match.get("guid")
                    if guid not in queue_guids or guid is None:
                        continue
                    timestamp = get_match_history_timestamp(match) or 0
                    if guid not in guid_timestamps or timestamp < guid_timestamps[guid]:
                        guid_timestamps[guid] = timestamp

            ordered_queue_guids = sorted(
                queue_guids,
                key=lambda guid: (guid_timestamps.get(guid, 0), str(guid))
            )
            seen_guids.update(ordered_queue_guids)

        for guid in queue_guids:
            if guid not in seen_guids:
                ordered_queue_guids.append(guid)
                seen_guids.add(guid)

        # Step 2: pull the public scoreboard for every identified round and aggregate per survev.de
        # player (keyed by slug when present, else username — covers accounts with no public slug).
        players: dict[str, dict] = {}
        # survev.de's team_id is allocated fresh per game (not stable across a series), so timeline
        # round winners must be tracked per identity here and resolved via that identity later.
        round_player_rank_by_guid: dict[str, dict[str, int]] = {}
        game_details_by_guid: dict[str, dict] = {}
        for guid in ordered_queue_guids:
            board = await fetch_public_match_data(session, guid)
            if not board:
                continue

            round_ranks = round_player_rank_by_guid.setdefault(guid, {})
            game_entry = game_details_by_guid.setdefault(guid, {"guid": guid, "players": []})
            for p in board:
                username = (p.get("username") or "").strip()
                slug = p.get("slug")
                key = slug or username.lower()
                if not key:
                    continue

                raw_rank = p.get("rank")
                parsed_rank = None
                if raw_rank is not None:
                    try:
                        parsed_rank = int(raw_rank)
                    except (TypeError, ValueError):
                        parsed_rank = None

                game_entry["players"].append({
                    "username": username,
                    "slug": slug,
                    "team_id": p.get("team_id"),
                    "rank": parsed_rank,
                    "kills": p.get("kills", 0),
                    "damage_dealt": p.get("damage_dealt", 0),
                })

                agg = players.setdefault(key, {
                    "username": username, "slug": slug, "team_id": p.get("team_id"),
                    "games": 0, "wins": 0, "kills": 0, "damage": 0, "best_rank": None
                })
                agg["games"] += 1

                agg["wins"] += 1 if parsed_rank == 1 else 0
                agg["kills"] += p.get("kills", 0)
                agg["damage"] += p.get("damage_dealt", 0)
                if parsed_rank is not None and (agg["best_rank"] is None or parsed_rank < agg["best_rank"]):
                    agg["best_rank"] = parsed_rank
                if parsed_rank is not None and (key not in round_ranks or parsed_rank < round_ranks[key]):
                    round_ranks[key] = parsed_rank

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

        # A game can involve more teams than just the queue's own (e.g. public matchmaking lobbies with
        # random other squads) — only show the top 2 places so unrelated teams never clutter the image.
        result_team_ids = [team_ids[i] for i in range(len(result_teams))]
        top_two_indices = set(range(len(result_teams)))
        if len(result_teams) > 2:
            team_best_rank = {}
            for tid in team_ids:
                ranks = [p["best_rank"] for p in players.values() if p["team_id"] == tid and p["best_rank"] is not None]
                team_best_rank[tid] = min(ranks) if ranks else 999
            top_two_indices = {
                team_id_index[tid]
                for tid in sorted(team_ids, key=lambda tid: team_best_rank[tid])[:2]
            }
            result_teams = [team for i, team in enumerate(result_teams) if i in top_two_indices]
            result_team_ids = [team_id for i, team_id in enumerate(result_team_ids) if i in top_two_indices]

        # Reorder to match NeatQueue's own Team 1/Team 2 display order, using known discord_ids as votes.
        def neatqueue_order_key(pair):
            team, _ = pair
            votes = [neatqueue_team_index_by_discord_id[e["discord_id"]] for e in team if e["discord_id"] in neatqueue_team_index_by_discord_id]
            if not votes:
                return len(teams)  # no known players on this team — push to the end
            return max(set(votes), key=votes.count)

        ordered_pairs = sorted(zip(result_teams, result_team_ids), key=neatqueue_order_key)
        if ordered_pairs:
            result_teams, result_team_ids = zip(*ordered_pairs)
            result_teams = [list(team) for team in result_teams]
            result_team_ids = list(result_team_ids)
        else:
            result_teams = []
            result_team_ids = []

        # Authoritative series score/winner comes from aggregated rank-1 wins per displayed team.
        # Every teammate on a team shares the same series win count, so take max to avoid duplicates.
        team_round_wins = [max((entry["stats"].get("wins", 0) for entry in team), default=0) for team in result_teams]

        winning_team_index = None
        if team_round_wins:
            max_wins = max(team_round_wins)
            if max_wins > 0 and team_round_wins.count(max_wins) == 1:
                winning_team_index = team_round_wins.index(max_wins)

        # Identity, not team_id, is what stays stable across a series' separate games.
        identity_to_display_index: dict[str, int] = {
            (entry["slug"] or entry["username"].lower()): idx
            for idx, team in enumerate(result_teams) for entry in team
        }
        round_winner_display_indices: list[int | None] = []
        for guid in ordered_queue_guids:
            round_ranks = round_player_rank_by_guid.get(guid, {})
            displayed_rank_one_winners = {
                identity_to_display_index[key]
                for key, rank in round_ranks.items()
                if rank == 1 and key in identity_to_display_index
            }

            # Only classify round when exactly one displayed team has rank 1 for that game.
            if len(displayed_rank_one_winners) == 1:
                round_winner_display_indices.append(next(iter(displayed_rank_one_winners)))
            else:
                round_winner_display_indices.append(None)

        individual_games: list[dict] = []
        for game_index, guid in enumerate(ordered_queue_guids, start=1):
            winner_idx = round_winner_display_indices[game_index - 1] if game_index - 1 < len(round_winner_display_indices) else None
            game_entry = game_details_by_guid.get(guid, {"guid": guid, "players": []})
            players_for_game = game_entry.get("players", [])
            players_for_game.sort(
                key=lambda player: (
                    player.get("rank") is None,
                    player.get("rank") if player.get("rank") is not None else 999,
                    -(player.get("damage_dealt") or 0),
                )
            )
            individual_games.append({
                "game_number": game_index,
                "guid": guid,
                "winning_display_team_index": winner_idx,
                "players": players_for_game,
            })

        match_history: list[bool | None] = []
        for winner_idx in round_winner_display_indices:
            if winner_idx is None or winning_team_index is None:
                match_history.append(None)
            else:
                match_history.append(winner_idx == winning_team_index)

        return {
            "teams": result_teams,
            "winning_team_index": winning_team_index,
            "match_history": match_history,
            "team_round_wins": team_round_wins,
            "total_games_played": len(ordered_queue_guids),
            "duration_ms": max(0, end_ms - start_ms),
            "individual_games": individual_games,
        }, None


# ------------------------------------------------------------------
# 4. SLASH COMMANDS
# ------------------------------------------------------------------
async def refresh_leaderboard_message(interaction: discord.Interaction, period: str, days: int, sort_by: str = "kills"):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
    embed = await generate_leaderboard_embed(period=period, days=days, sort_by=sort_by)
    await interaction.message.edit(embed=embed, view=LeaderboardView(period, sort_by))


class LeaderboardSortSelect(discord.ui.Select):
    def __init__(self, selected_sort: str = "kills"):
        sort_key = selected_sort if selected_sort in LEADERBOARD_SORT_CONFIG else "kills"
        options = [
            discord.SelectOption(label=cfg["label"], value=key, default=(key == sort_key))
            for key, cfg in LEADERBOARD_SORT_CONFIG.items()
        ]
        super().__init__(
            placeholder="Select leaderboard stat",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="leaderboard_sort"
        )

    async def callback(self, interaction: discord.Interaction):
        period = "Weekly"
        days = 7
        if isinstance(self.view, LeaderboardView):
            period = self.view.initial_period
            days = 30 if period == "Monthly" else 7
        selected_sort = self.values[0] if self.values else "kills"
        await refresh_leaderboard_message(interaction, period, days, selected_sort)


class LeaderboardView(discord.ui.View):
    def __init__(self, initial_period: str = "Weekly", initial_sort: str = "kills"):
        super().__init__(timeout=None)
        self.initial_period = initial_period
        self.initial_sort = initial_sort if initial_sort in LEADERBOARD_SORT_CONFIG else "kills"

        if initial_period == "Weekly":
            self.weekly_button.style = discord.ButtonStyle.primary
            self.monthly_button.style = discord.ButtonStyle.secondary
        else:
            self.weekly_button.style = discord.ButtonStyle.secondary
            self.monthly_button.style = discord.ButtonStyle.primary

        self.add_item(LeaderboardSortSelect(self.initial_sort))

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.primary, custom_id="leaderboard_weekly")
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await refresh_leaderboard_message(interaction, "Weekly", 7, self.initial_sort)

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id="leaderboard_monthly")
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await refresh_leaderboard_message(interaction, "Monthly", 30, self.initial_sort)


@bot.tree.command(name="leaderboard_weekly", description="View the top players over the past 7 days.")
async def leaderboard_weekly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed("Weekly", 7, "kills")
    await interaction.followup.send(embed=embed, view=LeaderboardView("Weekly", "kills"))


@bot.tree.command(name="leaderboard_monthly", description="View the top players over the past 30 days.")
async def leaderboard_monthly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed("Monthly", 30, "kills")
    await interaction.followup.send(embed=embed, view=LeaderboardView("Monthly", "kills"))


@bot.tree.command(name="leaderboard_fries", description="Rank users by their survev.de Golden Fries balance.")
async def leaderboard_fries(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, error = await build_leaderboard_fries_embed()
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send(embed=embed)


class QueueResultView(discord.ui.View):
    """Persistent view attached to queue-result messages, so anyone missing from the roster can self-serve
    a verification link (visible only to them, in the same channel) instead of having to know /verify exists."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Account unlinked?  Verify here for a gf.", style=discord.ButtonStyle.secondary, custom_id="queue_result_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG - verify_button callback invoked by {interaction.user}")
        await interaction.response.defer(ephemeral=True)
        print("DEBUG - verify_button deferred, starting verification flow")
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))
        print("DEBUG - verify_button verification flow finished")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        # discord.py's default View.on_error just logs to stderr, which is easy to miss — surface it
        # to the clicking user too so a failure is never silent.
        print(f"ERROR - QueueResultView item {item} failed: {error!r}")
        message = f"❌ Something went wrong: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


class InventoryRaritySelect(discord.ui.Select):
    def __init__(self, selected_rarity: str = "all", rarity_counts: dict[str, int] | None = None):
        rarity_key = normalize_inventory_rarity_filter(selected_rarity)
        counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        options = [
            discord.SelectOption(
                label=f"{label} ({counts.get(value, 0)})",
                value=value,
                default=(value == rarity_key)
            )
            for value, label in INVENTORY_RARITY_FILTERS.items()
        ]
        super().__init__(
            placeholder="Select rarity",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="inventory_rarity"
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class InventoryPaginationView(discord.ui.View):
    """Persistent view for paginating through inventory items."""
    def __init__(self, total_pages: int, rarity_filter: str = "all", rarity_counts: dict[str, int] | None = None):
        super().__init__(timeout=None)
        self.total_pages = total_pages
        self.rarity_filter = normalize_inventory_rarity_filter(rarity_filter)
        self.rarity_counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        self.add_item(InventoryRaritySelect(self.rarity_filter, self.rarity_counts))

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="inventory_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="inventory_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


# Store pagination state: msg_id -> (target_user, access_token, mode, current_page, total_pages)
inventory_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}

# Store market pagination state: msg_id -> (target_user, access_token, mode, current_page, total_pages)
market_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}


class ShopPaginationView(discord.ui.View):
    """Persistent view for paginating through shop offers."""
    def __init__(self, total_pages: int):
        super().__init__(timeout=None)
        self.total_pages = total_pages

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="market_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="market_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


async def refresh_market_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, mode: str, page: int, msg_id: int):
    """Refresh the market message with a new page."""
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        market_data, error = await fetch_user_market(session, access_token)
        if error or not market_data:
            await interaction.followup.send("Failed to reload shop offers.")
            return

        username = market_data.get("username") or str(target_user)
        offers = market_data.get("offers", [])
        
        # Filter by mode
        if mode == "daily":
            shown_offers = [o for o in offers if o.get("slot") in (0, 1)]
        elif mode == "weekly":
            shown_offers = [o for o in offers if o.get("slot") in (2, 3)]
        else:
            shown_offers = offers
        
        total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
        
        # Clamp page to valid range
        page = max(0, min(page, total_pages - 1))
        
        # Update state
        market_pagination_state[msg_id] = (target_user, access_token, mode, page, total_pages)
        
        # Generate new image
        image_buffer = generate_shop_image(username, market_data, mode, page)
        filename = f"shop_{target_user.id}_{mode}_p{page}.png"
        file = discord.File(image_buffer, filename=filename)
        
        # Create embed
        mode_label = "Daily" if mode == "daily" else "Weekly" if mode == "weekly" else "All"
        embed = discord.Embed(title=f"{username}'s {mode_label} Offers", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        
        # Create view with updated button states
        view = ShopPaginationView(total_pages)
        view.update_button_states(page)
        
        await interaction.message.edit(attachments=[file], embed=embed, view=view)


async def refresh_inventory_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, rarity_filter: str, page: int, msg_id: int):
    """Refresh the inventory message with a new page."""
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        inventory, error = await fetch_user_inventory(session, access_token)
        if error or not inventory:
            await interaction.followup.send("Failed to reload inventory.")
            return

        items = inventory.get("items", [])
        username = inventory.get("username") or str(target_user)
        rarity_key = normalize_inventory_rarity_filter(rarity_filter)
        grouped_items = group_inventory_items(items)
        rarity_counts = build_inventory_rarity_counts(grouped_items)
        if rarity_key != "all":
            rarity_value = int(rarity_key)
            grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
        total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
        
        # Clamp page to valid range
        page = max(0, min(page, total_pages - 1))
        
        # Update state
        inventory_pagination_state[msg_id] = (target_user, access_token, rarity_key, page, total_pages)
        
        # Generate new image
        image_buffer = generate_inventory_image(username, items, page, rarity_key)
        filename = f"inventory_{target_user.id}_p{page}.png"
        file = discord.File(image_buffer, filename=filename)
        
        # Create embed with image attached
        rarity_label_text = INVENTORY_RARITY_FILTERS[rarity_key]
        embed = discord.Embed(title=f"{username}'s Inventory ({rarity_label_text})", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        
        # Create view with updated button states
        view = InventoryPaginationView(total_pages, rarity_key, rarity_counts)
        view.update_button_states(page)
        
        await interaction.message.edit(attachments=[file], embed=embed, view=view)


queue_result_view = QueueResultView()


def check_queue_hall_of_fame_records(
    teams: list[list[dict]],
    match_id: str,
    guild_id: int,
    total_games_played: int,
    duration_ms: int | None = None,
    min_games_required: int = MIN_GAMES_FOR_HALL_OF_FAME_UPDATE,
) -> list[str]:
    """Scans a queue's final teams for new all-time bests, updates the DB, and returns announcement lines."""
    if total_games_played < min_games_required:
        return []

    best_kills: tuple[int, dict] | None = None
    best_avg_damage: tuple[float, dict] | None = None
    for team in teams:
        for entry in team:
            stats = entry["stats"]
            kills = stats.get("kills", 0)
            if best_kills is None or kills > best_kills[0]:
                best_kills = (kills, entry)

            games = stats.get("games", 0)
            if games > 0:
                avg_damage = stats["damage"] / games
                if best_avg_damage is None or avg_damage > best_avg_damage[0]:
                    best_avg_damage = (avg_damage, entry)

    announcements = []
    for record_type, candidate in (("most_kills", best_kills), ("most_avg_damage", best_avg_damage)):
        if candidate is None:
            continue
        value, entry = candidate
        holder_name = entry.get("display_name") or entry.get("username") or "Unknown"
        if try_set_hall_of_fame_record(record_type, value, entry.get("discord_id"), holder_name, match_id, guild_id):
            value_text = f"{value:,.0f}" if record_type == "most_avg_damage" else str(int(value))
            announcements.append(f"🏆 New Hall of Fame record! **{HALL_OF_FAME_RECORDS[record_type]}**: {value_text} by {holder_name}")

    if duration_ms is not None and duration_ms > 0:
        if try_set_hall_of_fame_record(
            "longest_queue",
            float(duration_ms),
            None,
            "Queue Duration",
            match_id,
            guild_id,
            duration_ms=duration_ms,
        ):
            announcements.append(
                f"🏆 New Hall of Fame record! **{HALL_OF_FAME_RECORDS['longest_queue']}**: {format_duration_ms(duration_ms)}"
            )
    return announcements


async def build_queue_stats_payload(match_id: str, guild_id: int):
    """Runs the NeatQueue/survev.de cross-reference and returns either
    (content, discord.File, None, match_result) on success or (None, None, error_text, None) on failure."""
    match_result, error = await calculate_queue_match_stats(match_id, guild_id)
    if error:
        return None, None, f"❌ {error}", None

    teams = match_result["teams"]
    if not any(teams):
        return None, None, "This NeatQueue match has no player roster to show stats for.", None

    await resolve_queue_user_display_names(teams, bot)
    image_buffer = generate_queue_result_image(
        match_id,
        teams,
        match_result["winning_team_index"],
        match_result.get("match_history"),
        match_result.get("team_round_wins"),
    )
    file = discord.File(image_buffer, filename=f"queue_stats_{match_id}.png")

    content = f"Queue stats for NeatQueue#{match_id}"
    total_games_played = int(match_result.get("total_games_played") or len(match_result.get("match_history") or []))
    record_announcements = check_queue_hall_of_fame_records(
        teams,
        match_id,
        guild_id,
        total_games_played,
        duration_ms=match_result.get("duration_ms"),
    )
    if record_announcements:
        content += "\n" + "\n".join(record_announcements)
    return content, file, None, match_result


@bot.tree.command(name="queue_stats", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queue_stats(interaction: discord.Interaction, match_id: str):
    await interaction.response.defer()

    if interaction.guild_id is None:
        await interaction.followup.send("❌ This command only works in a server.")
        return

    content, file, error_text, match_result = await build_queue_stats_payload(match_id, interaction.guild_id)
    write_queue_stats_command_log(interaction, match_id, match_result, error_text)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file, view=queue_result_view)


async def build_hall_of_fame_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏛️ NeatQueue Hall of Fame",
        description="All-time best single-queue stats recorded by /queue_stats.",
        color=discord.Color.gold()
    )
    for record_type, label in HALL_OF_FAME_RECORDS.items():
        record = get_hall_of_fame_record(record_type)
        if record is None:
            embed.add_field(name=label, value="No record set yet.", inline=False)
            continue
        if record_type == "most_avg_damage":
            value_text = f"{record['value']:,.0f}"
            holder = f"<@{record['discord_id']}>" if record["discord_id"] else record["display_name"]
            field_value = f"**{value_text}** — {holder} (Queue #{record['match_id']})"
        elif record_type == "longest_queue":
            raw_duration = record.get("duration_ms")
            if raw_duration is None:
                raw_duration = int(record["value"])
            value_text = format_duration_ms(int(raw_duration))
            field_value = f"**{value_text}** (Queue #{record['match_id']})"
        else:
            value_text = str(int(record["value"]))
            holder = f"<@{record['discord_id']}>" if record["discord_id"] else record["display_name"]
            field_value = f"**{value_text}** — {holder} (Queue #{record['match_id']})"

        embed.add_field(name=label, value=field_value, inline=False)
    embed.set_footer(text="Data courtesy of NeatQueue & survev.de APIs :)")
    return embed


@bot.tree.command(name="hall_of_fame", description="View the best single-queue stats ever recorded.")
async def hall_of_fame(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await build_hall_of_fame_embed()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="reset_hall_of_fame", description="Clear all Hall of Fame records.")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def reset_hall_of_fame(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    deleted_count = clear_hall_of_fame_records()
    await interaction.followup.send(
        f"✅ Hall of Fame reset complete. Removed {deleted_count} record(s).",
        ephemeral=True,
    )


@bot.tree.command(name="inventory", description="View a user's survev.de inventory")
@discord.app_commands.describe(member="Discord member whose inventory to display. Omit to use yourself.")
async def inventory(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = get_user_token(target.id)
    if not access_token:
        if target.id == interaction.user.id:
            await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` first.")
        else:
            await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
        return

    content, file, error_text, total_pages, filename, rarity_counts = await build_inventory_image_payload(target, access_token, 0, "all")
    if error_text:
        await interaction.followup.send(error_text)
        return

    # Create embed with image attached
    embed = discord.Embed(title=f"{content} (All Rarities)", color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page 1 of {total_pages or 1}")
    
    view = InventoryPaginationView(total_pages or 1, "all", rarity_counts)
    view.update_button_states(0)
    msg = await interaction.followup.send(embed=embed, file=file, view=view)
    
    # Store pagination state
    inventory_pagination_state[msg.id] = (target, access_token, "all", 0, total_pages or 1)


@bot.tree.command(name="goldenfries", description="View a user's survev.de Golden Fries balance.")
@discord.app_commands.describe(member="Discord member whose balance to display. Omit to use yourself.")
async def goldenfries(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = get_user_token(target.id)
    if not access_token:
        if target.id == interaction.user.id:
            await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
        else:
            await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
        return

    username, balance, error_text = await build_goldenfries_embed_payload(target, access_token)
    if error_text:
        await interaction.followup.send(error_text)
        return

    embed = discord.Embed(
        title="<:goldenfries:1535978920481136700> Golden Fries Balance",
        description=f"**{username}** currently has **{balance:,}** Golden Fries <:goldenfries:1535978920481136700>.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Data courtesy of survev.de API :)")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="compare", description="Compare two members' survev.de stats side-by-side.")
@discord.app_commands.describe(
    member_a="First Discord member to compare",
    member_b="Second Discord member to compare. Omit to compare yourself."
)
async def compare(
    interaction: discord.Interaction,
    member_a: discord.User,
    member_b: discord.User | None = None,
):
    member_b = member_b or interaction.user
    await interaction.response.defer()

    content, file, error_text = await build_compare_payload(member_a, member_b)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file)


class MarketGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="market", description="View a user's survev.de shop offers.")

    @discord.app_commands.command(name="daily", description="View only daily shop offers.")
    @discord.app_commands.describe(member="Discord member whose shop to display. Omit to use yourself.")
    async def daily(self, interaction: discord.Interaction, member: discord.User | None = None):
        target = member or interaction.user
        await interaction.response.defer()

        access_token = get_user_token(target.id)
        if not access_token:
            if target.id == interaction.user.id:
                await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
            else:
                await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
            return

        content, file, filename, error_text, total_pages = await build_shop_image_payload(target, access_token, mode="daily", page=0)
        if error_text:
            await interaction.followup.send(error_text)
            return

        # Create embed with image
        embed = discord.Embed(title=f"{content}", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page 1 of {total_pages}")

        # Create pagination view
        view = ShopPaginationView(total_pages)
        view.update_button_states(0)

        msg = await interaction.followup.send(embed=embed, file=file, view=view)
        
        # Store pagination state
        market_pagination_state[msg.id] = (target, access_token, "daily", 0, total_pages)

    @discord.app_commands.command(name="weekly", description="View only weekly shop offers.")
    @discord.app_commands.describe(member="Discord member whose shop to display. Omit to use yourself.")
    async def weekly(self, interaction: discord.Interaction, member: discord.User | None = None):
        target = member or interaction.user
        await interaction.response.defer()

        access_token = get_user_token(target.id)
        if not access_token:
            if target.id == interaction.user.id:
                await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
            else:
                await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
            return

        content, file, filename, error_text, total_pages = await build_shop_image_payload(target, access_token, mode="weekly", page=0)
        if error_text:
            await interaction.followup.send(error_text)
            return

        # Create embed with image
        embed = discord.Embed(title=f"{content}", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page 1 of {total_pages}")

        # Create pagination view
        view = ShopPaginationView(total_pages)
        view.update_button_states(0)

        msg = await interaction.followup.send(embed=embed, file=file, view=view)
        
        # Store pagination state
        market_pagination_state[msg.id] = (target, access_token, "weekly", 0, total_pages)


bot.tree.add_command(MarketGroup())


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
        return None

    configured_channel_id = get_guild_queue_channel(message.guild.id)
    if configured_channel_id is None or message.channel.id != configured_channel_id:
        return None

    for embed in message.embeds:
        if not embed.title:
            continue
        for title_pattern in title_patterns:
            match = title_pattern.search(embed.title)
            if match:
                return match.group(1)

    return None


async def post_queue_result(message: discord.Message, match_id: str):
    """Marks match_id as processed for this guild and, if new, replies to message with its stats."""
    if not try_mark_match_processed(message.guild.id, match_id):
        return  # already posted (e.g. the panel got edited again after a revert)

    # Give the game server a moment to finish writing this match's results before querying survev.de.
    await asyncio.sleep(QUEUE_RESULT_FETCH_DELAY_SECONDS)

    content, file, error_text, _ = await build_queue_stats_payload(match_id, message.guild.id)
    if error_text:
        await message.reply(error_text)
    else:
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
            matches, error = await fetch_neatqueue_matches_since(session, guild_id, last_updated)
            if error:
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

                content, file, error_text, _ = await build_queue_stats_payload(match_id, guild_id)
                if error_text:
                    continue  # nothing worth posting (e.g. no verified players), skip silently on catch-up
                await channel.send(content=f"*(Catching up)* {content}", file=file, view=queue_result_view)

            update_guild_last_updated(guild_id, datetime.now(timezone.utc).isoformat())


# --- RUN BOT ---
bot.run(DISCORD_BOT_TOKEN)