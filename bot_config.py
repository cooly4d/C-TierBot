"""Environment configuration, constants, and the shared bot/intents instance.

Every other module imports `bot` from here instead of constructing its own
discord.py client, so there is exactly one Bot instance for the whole app.
"""
import os
import re

import discord
from discord.ext import commands
from dotenv import load_dotenv

# --- CONFIGURATION ---

load_dotenv()

LEADERBOARD_BANNER_URL = os.getenv("LEADERBOARD_BANNER_URL", "https://media.giphy.com/media/3o6ZtpxSZbQ2zYpH0A/giphy.gif")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SURVEV_CLIENT_ID = os.getenv("SURVEV_CLIENT_ID")
SURVEV_CLIENT_SECRET = os.getenv("SURVEV_CLIENT_SECRET")
NEATQUEUE_API_TOKEN = os.getenv("NEATQUEUE_API_TOKEN")
NEATQUEUE_API_BASE = "https://api.neatqueue.com/api/v1"
NEATQUEUE_BOT_ID = int(os.getenv("NEATQUEUE_BOT_ID", "857633321064595466"))
QUEUE_RESULT_FETCH_DELAY_SECONDS = int(os.getenv("QUEUE_RESULT_FETCH_DELAY_SECONDS", "5"))
# NeatQueue messages older than this are never (re)processed live; NeatQueue occasionally mass-edits old
# result messages, which would otherwise trigger a burst of replies for matches played long ago.
QUEUE_RESULT_MAX_MESSAGE_AGE_SECONDS = int(os.getenv("QUEUE_RESULT_MAX_MESSAGE_AGE_HOURS", "24")) * 3600
# Last-resort window length when NeatQueue gives us no usable end-of-match signal at all.
QUEUE_MATCH_FALLBACK_DURATION_MS = int(os.getenv("QUEUE_MATCH_FALLBACK_DURATION_MINUTES", "10")) * 60 * 1000
# How many survev.de requests may run at once. survev.de's limits count requests per minute (60/min for the bot's
# OAuth app, 40/min per IP for scoreboards), not concurrent ones, so this only sets how fast a lookup goes: at 10, a
# 4v4 queue's player histories and then its scoreboards usually go out in one batch each instead of two.
API_CONCURRENCY_LIMIT = int(os.getenv("API_CONCURRENCY_LIMIT", "10"))
VERIFICATION_TIMEOUT_SECONDS = int(os.getenv("VERIFICATION_TIMEOUT_SECONDS", "300"))
# Console log level (DEBUG also shows every button/menu click). Diagnostic JSON logs go to QUEUE_STATS_LOG_DIR.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
QUEUE_STATS_LOG_DIR = "log"
QUEUE_STATS_LOG_MAX_FILES = int(os.getenv("QUEUE_STATS_LOG_MAX_FILES", "200"))
# Loopback port for console.py, the host console inside a container with no attached stdin.
# 0 (the default) turns it off; the Dockerfile sets 8765. It only ever binds 127.0.0.1.
CONSOLE_PORT = int(os.getenv("CONSOLE_PORT", "0"))

# NeatQueue's final results announcement, e.g. "🏆 Winner For Queue#3674 🏆" — already final when posted.
QUEUE_WINNER_TITLE_PATTERN = re.compile(r"Winner For Queue#(\d+)", re.IGNORECASE)
# NeatQueue's admin queue panel, e.g. "Results for Queue#3889" — posted at queue start with no result yet,
# then edited in place once the queue finishes. Only the edit carries a real result.
QUEUE_PANEL_TITLE_PATTERN = re.compile(r"Results for Queue#(\d+)", re.IGNORECASE)

# Every single-queue stat tracked in the /hall_of_fame board, keyed by DB record_type -> display label.
HALL_OF_FAME_RECORDS = {
    "most_kills": "Most Kills in a Queue",
    "most_avg_damage": "Most Avg Damage in a Queue",
    "longest_queue": "Longest Queue Duration",
}
MIN_GAMES_FOR_HALL_OF_FAME_UPDATE = 4

LEADERBOARD_SORT_CONFIG = {
    "kills": {"label": "Kills", "emoji": "⚔️"},
    "wins": {"label": "Wins", "emoji": "🏆"},
    "games": {"label": "Games", "emoji": "🎮"},
    "damage": {"label": "Damage", "emoji": "💥"},
}

# Words that make the bot react to a message -> the emoji it reacts with. A word counts anywhere in the
# message, in any letter case ("Cooly", "coolyyy"). The emojis are application emojis uploaded to the bot's
# Emojis tab in the Discord Developer Portal, so they work in every server without a server emoji slot.
KEYWORD_REACTIONS = {
    "cooly": "<a:cooly:1534718488839458977>",
}

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed so client.get_all_members() has data — required for real display names
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
