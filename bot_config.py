"""Environment configuration, constants, and the shared bot/intents instance.

Every other module imports `bot` from here instead of constructing its own
discord.py client, so there is exactly one Bot instance for the whole app.
"""
import os
import re

import discord
from discord.ext import commands
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
QUEUE_STATS_LOG_DIR = "log"
#all supposed to be environment variables by cba

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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed so client.get_all_members() has data — required for real display names
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
