"""SQLite persistence layer: schema setup plus every read/write helper.

Every function opens its own short-lived connection (`with sqlite3.connect(...)`)
rather than sharing a module-level connection, matching the original bot's
behavior so nothing about transaction/commit timing changes.
"""
import sqlite3
from datetime import datetime, timezone

DB_PATH = "leaderboard.db"

# --- Schema setup (runs once at import time) ---
conn = sqlite3.connect(DB_PATH)
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
# Cached per-player queue results used by /profile.
cursor.execute('''
    CREATE TABLE IF NOT EXISTS player_queue_stats (
        guild_id INTEGER NOT NULL,
        match_id TEXT NOT NULL,
        player_key TEXT NOT NULL,
        discord_id INTEGER,
        display_name TEXT,
        username TEXT,
        slug TEXT,
        team_index INTEGER NOT NULL,
        total_damage INTEGER NOT NULL,
        avg_damage REAL NOT NULL,
        duration_ms INTEGER NOT NULL,
        match_start_ms INTEGER,
        match_end_ms INTEGER,
        cached_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, match_id, player_key)
    )
''')
# Older DBs created before duration tracking existed — add it on if missing.
try:
    cursor.execute("ALTER TABLE hall_of_fame ADD COLUMN duration_ms INTEGER")
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_queue_stats_discord_id ON player_queue_stats(discord_id)")
except sqlite3.OperationalError:
    pass
conn.commit()


# Helper: Save User Token (+ their survev.de slug/username, so we can recognize them by slug later)
def save_token(discord_id: int, token: str, slug: str | None = None, username: str | None = None):
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO users (discord_id, access_token, slug, username) VALUES (?, ?, ?, ?)",
            (discord_id, token, slug, username)
        )

# Helper: Get All User Tokens
def get_all_users():
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT discord_id, access_token FROM users").fetchall()

# Helper: Look up which verified Discord user owns a given survev.de slug, if any
def get_discord_id_by_slug(slug: str):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT discord_id FROM users WHERE slug = ?", (slug,)).fetchone()
        return row[0] if row else None

# Helper: Get every verified user whose slug we haven't captured yet (e.g. verified before that existed)
def get_users_missing_slug():
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT discord_id, access_token FROM users WHERE slug IS NULL").fetchall()

# Helper: Fill in a previously-unknown slug/username for an already-verified user
def update_user_slug(discord_id: int, slug: str, username: str):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE users SET slug = ?, username = ? WHERE discord_id = ?", (slug, username, discord_id))

# Helper: Get Single User Token
def get_user_token(discord_id: int):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT access_token FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        return row[0] if row else None

# Helper: Set the channel a guild wants NeatQueue results tracked in
def set_guild_queue_channel(guild_id: int, channel_id: int):
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, channel_id, last_updated) VALUES (?, ?, ?)",
            (guild_id, channel_id, datetime.now(timezone.utc).isoformat())
        )

# Helper: Get the channel configured for a guild, if any
def get_guild_queue_channel(guild_id: int):
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute("SELECT channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
        return row[0] if row else None

# Helper: Get every guild's configured channel + the last time its queue results were caught up on
def get_all_guild_settings():
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT guild_id, channel_id, last_updated FROM guild_settings").fetchall()

# Helper: Mark a guild as caught up as of the given timestamp, without touching its channel
def update_guild_last_updated(guild_id: int, timestamp_iso: str):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE guild_settings SET last_updated = ? WHERE guild_id = ?", (timestamp_iso, guild_id))

# Helper: Record that a match's stats were posted. Returns False if it was already recorded (skip re-posting).
def try_mark_match_processed(guild_id: int, match_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO processed_matches (guild_id, match_id, processed_at) VALUES (?, ?, ?)",
            (guild_id, str(match_id), datetime.now(timezone.utc).isoformat())
        )
        return cur.rowcount > 0


# Helper: Look up the current holder of a hall of fame record, if any has been set yet.
def get_hall_of_fame_record(record_type: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as c:
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
    with sqlite3.connect(DB_PATH) as c:
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
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute("DELETE FROM hall_of_fame")
        return cur.rowcount if cur.rowcount is not None else 0


def is_current_utc_month(timestamp_ms: int | None) -> bool:
    if timestamp_ms is None:
        return False

    timestamp_dt = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    return timestamp_dt.year == now.year and timestamp_dt.month == now.month


def cache_player_queue_stats(match_id: str, guild_id: int, match_result: dict) -> int:
    """Caches finalized per-player queue stats for /profile when the match is eligible."""
    if not match_result.get("is_final"):
        return 0

    if not is_current_utc_month(match_result.get("match_end_ms")):
        return 0

    teams = match_result.get("teams") or []
    total_players = sum(len(team) for team in teams)
    if total_players <= 4:
        return 0

    duration_ms = match_result.get("duration_ms")
    if duration_ms is None:
        return 0

    cached_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for team_index, team in enumerate(teams):
        for entry in team:
            stats = entry.get("stats") or {}
            discord_id = entry.get("discord_id")
            username = entry.get("username")
            slug = entry.get("slug")
            display_name = entry.get("display_name") or username or "Unknown"
            if discord_id is not None:
                player_key = f"discord:{discord_id}"
            else:
                player_key = f"guest:{slug or username or display_name}"

            games = int(stats.get("games") or 0)
            total_damage = int(stats.get("damage") or 0)
            avg_damage = total_damage / games if games > 0 else 0.0

            rows.append((
                guild_id,
                str(match_id),
                player_key,
                discord_id,
                display_name,
                username,
                slug,
                team_index,
                total_damage,
                avg_damage,
                int(duration_ms),
                match_result.get("match_start_ms"),
                match_result.get("match_end_ms"),
                cached_at,
            ))

    if not rows:
        return 0

    with sqlite3.connect(DB_PATH) as c:
        c.executemany(
            """
            INSERT OR REPLACE INTO player_queue_stats (
                guild_id, match_id, player_key, discord_id, display_name, username, slug,
                team_index, total_damage, avg_damage, duration_ms, match_start_ms, match_end_ms, cached_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    return len(rows)


def get_player_queue_duration_summary(discord_id: int) -> tuple[int, int]:
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(duration_ms), 0) FROM player_queue_stats WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()
        if row is None:
            return 0, 0
        return int(row[0] or 0), int(row[1] or 0)
