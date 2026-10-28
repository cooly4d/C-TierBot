"""SQLite persistence layer: schema setup plus every read/write helper.

Every function opens its own short-lived connection (`with sqlite3.connect(...)`)
rather than sharing a module-level connection, matching the original bot's
behavior so nothing about transaction/commit timing changes.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = "leaderboard.db"

# Per-game snapshots of finished queues, kept for the Game breakdown button on queue results.
QUEUE_RESULT_RETENTION_DAYS = 90
QUEUE_RESULTS_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS queue_results (
        guild_id INTEGER NOT NULL,
        match_id TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, match_id)
    )
'''

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
# Older DBs had guild_id as the sole primary key (one channel per guild) — rebuild with a composite key.
_guild_settings_pk = {row[1] for row in cursor.execute("PRAGMA table_info(guild_settings)").fetchall() if row[5]}
if _guild_settings_pk == {"guild_id"}:
    cursor.execute("ALTER TABLE guild_settings RENAME TO guild_settings_old")
cursor.execute('''
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        last_updated TEXT NOT NULL,
        PRIMARY KEY (guild_id, channel_id)
    )
''')
if _guild_settings_pk == {"guild_id"}:
    cursor.execute("INSERT INTO guild_settings SELECT guild_id, channel_id, last_updated FROM guild_settings_old")
    cursor.execute("DROP TABLE guild_settings_old")
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
        games INTEGER,
        duration_ms INTEGER NOT NULL,
        match_start_ms INTEGER,
        match_end_ms INTEGER,
        cached_at TEXT NOT NULL,
        kills INTEGER,
        impact REAL,
        winning_team_index INTEGER,
        team_round_wins TEXT,
        PRIMARY KEY (guild_id, match_id, player_key)
    )
''')
cursor.execute(QUEUE_RESULTS_SCHEMA)
# Older databases retain their cached rows; their game counts can be inferred
# for positive-damage rows when the seasonal leaderboard reads them.
for col_def in (
    "games INTEGER",
    "kills INTEGER",
    "impact REAL",
    "winning_team_index INTEGER",
    "team_round_wins TEXT",
):
    try:
        cursor.execute(f"ALTER TABLE player_queue_stats ADD COLUMN {col_def}")
    except sqlite3.OperationalError:
        pass
# Older DBs created before duration tracking existed — add it on if missing.
try:
    cursor.execute("ALTER TABLE hall_of_fame ADD COLUMN duration_ms INTEGER")
except sqlite3.OperationalError:
    pass
try:
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_queue_stats_discord_id ON player_queue_stats(discord_id)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_queue_stats_guild_discord "
        "ON player_queue_stats(guild_id, discord_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_player_queue_stats_guild_match_end "
        "ON player_queue_stats(guild_id, match_end_ms)"
    )
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

# Helper: Add a channel a guild wants NeatQueue results tracked in (no-op if already configured)
def add_guild_queue_channel(guild_id: int, channel_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO guild_settings (guild_id, channel_id, last_updated) VALUES (?, ?, ?)",
            (guild_id, channel_id, datetime.now(timezone.utc).isoformat())
        )
        return cur.rowcount > 0

# Helper: Stop tracking a channel. Returns False if it wasn't configured.
def remove_guild_queue_channel(guild_id: int, channel_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute("DELETE FROM guild_settings WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id))
        return cur.rowcount > 0

# Helper: Get every channel configured for a guild
def get_guild_queue_channels(guild_id: int) -> list[int]:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute("SELECT channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchall()
        return [row[0] for row in rows]

# Helper: Get every guild's configured channel + the last time its queue results were caught up on
def get_all_guild_settings():
    with sqlite3.connect(DB_PATH) as c:
        return c.execute("SELECT guild_id, channel_id, last_updated FROM guild_settings").fetchall()

# Helper: Mark all of a guild's channels as caught up as of the given timestamp
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
    """Caches finalized per-player queue stats for /profile and /history when the match is eligible."""
    if not match_result.get("is_final"):
        return 0

    teams = match_result.get("teams") or []
    total_players = sum(len(team) for team in teams)
    if total_players <= 4:
        return 0

    duration_ms = match_result.get("duration_ms")
    if duration_ms is None:
        return 0

    cached_at = datetime.now(timezone.utc).isoformat()
    winning_team_index = match_result.get("winning_team_index")
    team_round_wins_list = match_result.get("team_round_wins")
    team_round_wins = json.dumps(team_round_wins_list) if team_round_wins_list is not None else None

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
            kills = int(stats.get("kills") or 0)
            raw_impact = stats.get("impact")
            impact = float(raw_impact) if raw_impact is not None else None

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
                games,
                int(duration_ms),
                match_result.get("match_start_ms"),
                match_result.get("match_end_ms"),
                cached_at,
                kills,
                impact,
                winning_team_index,
                team_round_wins,
            ))

    if not rows:
        return 0

    with sqlite3.connect(DB_PATH) as c:
        c.executemany(
            """
            INSERT OR REPLACE INTO player_queue_stats (
                guild_id, match_id, player_key, discord_id, display_name, username, slug,
                team_index, total_damage, avg_damage, games, duration_ms, match_start_ms, match_end_ms, cached_at,
                kills, impact, winning_team_index, team_round_wins
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    return len(rows)


def get_player_queue_history(guild_id: int, discord_id: int, limit: int = 10) -> list[dict]:
    """Returns a player's most recent finalized queues (newest first) up to `limit`.

    Legacy rows with missing kills or result metadata are automatically enriched
    from saved queue_results snapshots when available.
    """
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            """
            SELECT match_id, team_index, total_damage, avg_damage, games,
                   duration_ms, match_start_ms, match_end_ms, display_name, username,
                   kills, impact, winning_team_index, team_round_wins
            FROM player_queue_stats
            WHERE guild_id = ? AND discord_id = ?
            ORDER BY COALESCE(match_end_ms, match_start_ms, 0) DESC,
                     CAST(match_id AS INTEGER) DESC,
                     match_id DESC
            LIMIT ?
            """,
            (guild_id, discord_id, limit),
        ).fetchall()

    history = []
    for row in rows:
        match_id = str(row[0])
        team_index = int(row[1])
        total_damage = int(row[2])
        avg_damage = float(row[3])
        games = row[4]
        duration_ms = row[5]
        match_start_ms = row[6]
        match_end_ms = row[7]
        display_name = row[8]
        username = row[9]
        kills = row[10]
        impact = row[11]
        winning_team_index = row[12]
        team_round_wins_raw = row[13]

        team_round_wins = None
        if team_round_wins_raw:
            try:
                parsed_wins = json.loads(team_round_wins_raw)
                if isinstance(parsed_wins, list):
                    team_round_wins = [int(w) for w in parsed_wins]
            except (ValueError, TypeError):
                team_round_wins = None

        # Backfill from queue_results if legacy row is missing kills, winning_team_index, etc.
        if kills is None or winning_team_index is None or team_round_wins is None or impact is None:
            snapshot = get_queue_result(guild_id, match_id)
            if snapshot:
                if winning_team_index is None:
                    winning_team_index = snapshot.get("winning_team_index")
                if team_round_wins is None:
                    parsed_snapshot_wins = snapshot.get("team_round_wins")
                    if isinstance(parsed_snapshot_wins, list):
                        team_round_wins = [int(w) for w in parsed_snapshot_wins]

                if kills is None or impact is None:
                    snap_kills = 0
                    snap_impact_scores = []
                    found_player = False
                    for g in snapshot.get("games") or []:
                        for p in g.get("players") or []:
                            if (p.get("discord_id") == discord_id) or (
                                p.get("name") and p.get("name") in (display_name, username)
                            ):
                                found_player = True
                                snap_kills += int(p.get("kills") or 0)
                                if p.get("impact") is not None:
                                    snap_impact_scores.append(float(p["impact"]))
                    if found_player:
                        if kills is None:
                            kills = snap_kills
                        if impact is None and snap_impact_scores:
                            impact = sum(snap_impact_scores) / len(snap_impact_scores)

        won = None
        if winning_team_index is not None and 0 <= winning_team_index:
            won = (winning_team_index == team_index)

        history.append({
            "match_id": match_id,
            "team_index": team_index,
            "winning_team_index": winning_team_index,
            "team_round_wins": team_round_wins,
            "won": won,
            "games": games,
            "kills": kills,
            "total_damage": total_damage,
            "avg_damage": avg_damage,
            "impact": impact,
            "duration_ms": duration_ms,
            "match_start_ms": match_start_ms,
            "match_end_ms": match_end_ms,
            "display_name": display_name,
            "username": username,
        })

    return history


def get_seasonal_queue_stats(guild_id: int, month_start_ms: int, next_month_start_ms: int) -> list[dict]:
    """Returns verified players' weighted damage averages for a UTC calendar month.

    Rows with no positive damage are ignored. Legacy rows infer games from their
    stored average when possible; newly cached rows use the explicit games count.
    """
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            """
            WITH eligible_rows AS (
                SELECT discord_id, display_name, username, total_damage,
                       CASE
                           WHEN games IS NOT NULL THEN games
                           WHEN avg_damage > 0 THEN CAST(ROUND(total_damage / avg_damage) AS INTEGER)
                           ELSE NULL
                       END AS game_count
                FROM player_queue_stats
                WHERE guild_id = ?
                  AND discord_id IS NOT NULL
                  AND total_damage > 0
                  AND match_end_ms >= ?
                  AND match_end_ms < ?
            )
            SELECT discord_id,
                   MAX(display_name),
                   MAX(username),
                   SUM(total_damage),
                   SUM(game_count),
                   1.0 * SUM(total_damage) / SUM(game_count)
            FROM eligible_rows
            WHERE game_count > 0
            GROUP BY discord_id
            ORDER BY 1.0 * SUM(total_damage) / SUM(game_count) DESC, discord_id
            """,
            (guild_id, month_start_ms, next_month_start_ms),
        ).fetchall()

    return [
        {
            "discord_id": row[0],
            "display_name": row[1],
            "username": row[2],
            "total_damage": int(row[3]),
            "games": int(row[4]),
            "avg_damage": float(row[5]),
        }
        for row in rows
    ]


def get_player_queue_stats_summary(discord_id: int) -> dict:
    """All-time and current-month summary of a player's cached queue stats.

    Average damage and average impact are per game: rows store per-queue total_damage,
    the per-game avg_damage, and per-game impact. Games played is recovered from games
    or total_damage / avg_damage.
    """
    now = datetime.now(timezone.utc)
    month_start_ms = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000)

    # Games per queue (fallback to total_damage / avg_damage when explicit games is NULL).
    games_expr = (
        "CASE "
        "WHEN games IS NOT NULL AND games > 0 THEN games "
        "WHEN avg_damage > 0 THEN total_damage / avg_damage "
        "ELSE 0 END"
    )
    # Scored games for impact: only rows where impact IS NOT NULL contribute.
    impact_games_expr = (
        "CASE "
        "WHEN impact IS NOT NULL THEN ("
        "  CASE "
        "  WHEN games IS NOT NULL AND games > 0 THEN games "
        "  WHEN avg_damage > 0 THEN total_damage / avg_damage "
        "  ELSE 1 END"
        ") "
        "ELSE 0 END"
    )
    impact_points_expr = (
        "CASE "
        "WHEN impact IS NOT NULL THEN impact * ("
        "  CASE "
        "  WHEN games IS NOT NULL AND games > 0 THEN games "
        "  WHEN avg_damage > 0 THEN total_damage / avg_damage "
        "  ELSE 1 END"
        ") "
        "ELSE 0 END"
    )

    with sqlite3.connect(DB_PATH) as c:
        all_time = c.execute(
            f"""
            SELECT COUNT(*), COALESCE(SUM(duration_ms), 0),
                   COALESCE(SUM(total_damage), 0), COALESCE(SUM({games_expr}), 0),
                   COALESCE(SUM({impact_points_expr}), 0), COALESCE(SUM({impact_games_expr}), 0)
            FROM player_queue_stats
            WHERE discord_id = ?
            """,
            (discord_id,),
        ).fetchone()
        monthly = c.execute(
            f"""
            SELECT COALESCE(SUM(total_damage), 0), COALESCE(SUM({games_expr}), 0),
                   COALESCE(SUM({impact_points_expr}), 0), COALESCE(SUM({impact_games_expr}), 0)
            FROM player_queue_stats
            WHERE discord_id = ? AND match_end_ms >= ?
            """,
            (discord_id, month_start_ms),
        ).fetchone()

    matches = int(all_time[0] or 0)
    total_duration_ms = int(all_time[1] or 0)
    all_time_damage = float(all_time[2] or 0)
    all_time_games = float(all_time[3] or 0)
    all_time_impact_points = float(all_time[4] or 0)
    all_time_impact_games = float(all_time[5] or 0)

    monthly_damage = float(monthly[0] or 0)
    monthly_games = float(monthly[1] or 0)
    monthly_impact_points = float(monthly[2] or 0)
    monthly_impact_games = float(monthly[3] or 0)

    return {
        "matches": matches,
        "total_duration_ms": total_duration_ms,
        "all_time_avg_damage": all_time_damage / all_time_games if all_time_games > 0 else None,
        "monthly_avg_damage": monthly_damage / monthly_games if monthly_games > 0 else None,
        "all_time_avg_impact": all_time_impact_points / all_time_impact_games if all_time_impact_games > 0 else None,
        "monthly_avg_impact": monthly_impact_points / monthly_impact_games if monthly_impact_games > 0 else None,
    }


def save_queue_result(guild_id: int, match_id: str, snapshot: dict, retention_days: int = QUEUE_RESULT_RETENTION_DAYS):
    """Stores a finished queue's per-game snapshot (replacing any earlier one for that queue) and drops
    snapshots older than the retention window so the table can't grow without bound."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT OR REPLACE INTO queue_results (guild_id, match_id, snapshot, saved_at) VALUES (?, ?, ?, ?)",
            (guild_id, str(match_id), json.dumps(snapshot, separators=(",", ":")), now.isoformat()),
        )
        c.execute("DELETE FROM queue_results WHERE saved_at < ?", (cutoff,))


def get_queue_result(guild_id: int, match_id: str) -> dict | None:
    """The stored per-game snapshot for a queue, or None if it was never saved, expired, or is unreadable."""
    with sqlite3.connect(DB_PATH) as c:
        row = c.execute(
            "SELECT snapshot FROM queue_results WHERE guild_id = ? AND match_id = ?",
            (guild_id, str(match_id)),
        ).fetchone()
    if row is None:
        return None
    try:
        snapshot = json.loads(row[0])
    except ValueError:
        return None
    return snapshot if isinstance(snapshot, dict) else None
