"""Synthetic SQLite databases for the website tests (same tables and columns as db.py creates)."""
import json
import sqlite3
from datetime import datetime, timezone

HOME = 1363265113880264745
OTHER = 1075111506548625438
CANARY_TOKEN = "CANARY-TOKEN-7f3c9e1d"

SCHEMA = [
    """CREATE TABLE users (discord_id INTEGER PRIMARY KEY, access_token TEXT NOT NULL, slug TEXT, username TEXT)""",
    """CREATE TABLE guild_settings (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, last_updated TEXT NOT NULL,
       PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE processed_matches (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, processed_at TEXT NOT NULL,
       PRIMARY KEY (guild_id, match_id))""",
    """CREATE TABLE hall_of_fame (record_type TEXT PRIMARY KEY, value REAL NOT NULL, discord_id INTEGER,
       display_name TEXT, match_id TEXT NOT NULL, guild_id INTEGER NOT NULL, achieved_at TEXT NOT NULL,
       duration_ms INTEGER, game_number INTEGER)""",
    """CREATE TABLE player_queue_stats (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, player_key TEXT NOT NULL,
       discord_id INTEGER, display_name TEXT, username TEXT, slug TEXT, team_index INTEGER NOT NULL,
       total_damage INTEGER NOT NULL, avg_damage REAL NOT NULL, games INTEGER, duration_ms INTEGER NOT NULL,
       match_start_ms INTEGER, match_end_ms INTEGER, cached_at TEXT NOT NULL, kills INTEGER, impact REAL,
       winning_team_index INTEGER, team_round_wins TEXT, PRIMARY KEY (guild_id, match_id, player_key))""",
    """CREATE TABLE queue_results (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, snapshot TEXT NOT NULL,
       saved_at TEXT NOT NULL, PRIMARY KEY (guild_id, match_id))""",
]


def ms(year, month, day, hour=19):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for sql in SCHEMA:
        conn.execute(sql)
    conn.commit()
    return conn


def add_user(conn, discord_id, slug, username, token=CANARY_TOKEN):
    conn.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", (discord_id, token, slug, username))


def add_row(conn, guild_id, match_id, *, discord_id=None, name="Player", username=None, slug=None, team=0,
            damage=6000, games=6, kills=12, impact=60.0, end_ms=None, start_ms=None, duration_ms=3_600_000,
            winner=0, score=(4, 2), avg=None):
    username = username or name
    key = f"discord:{discord_id}" if discord_id is not None else f"guest:{slug or username}"
    end_ms = end_ms or ms(2026, 11, 20)
    avg_damage = avg if avg is not None else (damage / games if games else 0.0)
    conn.execute(
        "INSERT OR REPLACE INTO player_queue_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (guild_id, str(match_id), key, discord_id, name, username, slug, team, damage, avg_damage, games,
         duration_ms, start_ms or end_ms - duration_ms, end_ms, "2026-11-20T19:00:00+00:00", kills, impact, winner,
         json.dumps(list(score)) if score is not None else None),
    )


def add_snapshot(conn, guild_id, match_id, games, *, winner=0, score=(4, 2), v=2, saved_at="2026-11-20T19:05:00+00:00"):
    snap = {"v": v, "match_id": str(match_id), "winning_team_index": winner,
            "team_round_wins": list(score) if score is not None else None, "match_history": None, "games": games}
    conn.execute("INSERT OR REPLACE INTO queue_results VALUES (?, ?, ?, ?)",
                 (guild_id, str(match_id), json.dumps(snap), saved_at))


def game(number, winner, players):
    return {"number": number, "winner": winner, "players": players}


def prow(team, name, *, discord_id=None, slug=None, kills=2, damage=1000, impact=60, key=None):
    row = {"team": team, "name": name, "discord_id": discord_id, "guest": discord_id is None,
           "kills": kills, "damage": damage, "impact": impact, "username": name, "slug": slug}
    row["key"] = key or (f"slug:{slug}" if slug else f"name:{name}")
    return row


def standard_db(path: str) -> sqlite3.Connection:
    """Two linked players per team plus a guest; queue 4821 (snapshot + rows), 4700 (rows only), 4999 (snapshot
    only); a planted other-guild queue 4821; Hall of Fame records in both guilds."""
    conn = make_db(path)
    ids = {"kestrel": 111111111111111111, "juno": 222222222222222222, "moonpie": 333333333333333333,
           "okra": 444444444444444444}
    for slug, did in ids.items():
        add_user(conn, did, slug, slug.capitalize())
    games = []
    winners = [0, 1, 0, 0, 1, 0]
    for n, w in enumerate(winners, start=1):
        games.append(game(n, w, [
            prow(0, "KestrelNick", discord_id=ids["kestrel"], slug="kestrel", kills=4, damage=1500, impact=78),
            prow(0, "moonpieNick", discord_id=ids["moonpie"], slug="moonpie", kills=3, damage=1300, impact=66),
            prow(1, "JunoNick", discord_id=ids["juno"], slug="juno", kills=3, damage=1400, impact=74),
            prow(1, "sl1ce", kills=1, damage=800, impact=44),
        ]))
    add_snapshot(conn, HOME, 4821, games, winner=0, score=(4, 2))
    for slug, team, dmg, k, imp in (("kestrel", 0, 9000, 24, 78), ("moonpie", 0, 7800, 18, 66),
                                    ("juno", 1, 8400, 18, 74)):
        add_row(conn, HOME, 4821, discord_id=ids[slug], name=f"{slug.capitalize()}Nick", slug=slug, team=team,
                damage=dmg, kills=k, impact=imp)
    add_row(conn, HOME, 4821, name="sl1ce", team=1, damage=4800, kills=6, impact=44)
    for slug, team in (("kestrel", 0), ("okra", 1)):
        add_row(conn, HOME, 4700, discord_id=ids[slug], name=f"{slug.capitalize()}Old", slug=slug, team=team,
                damage=5000, kills=10, impact=None, end_ms=ms(2026, 11, 10), winner=1, score=(1, 4))
    add_snapshot(conn, HOME, 4999, [game(1, 0, [prow(0, "OnlyInSnapshot", kills=1, damage=500),
                                                 prow(1, "OtherGuest", kills=0, damage=300)])],
                 winner=0, score=(1, 0), saved_at="2026-11-21T10:00:00+00:00")
    add_row(conn, OTHER, 4821, discord_id=ids["okra"], name="OTHERGUILDSECRETNAME", slug="okra", team=0)
    add_snapshot(conn, OTHER, 4821, [game(1, 0, [prow(0, "OTHERGUILDSECRETNAME", discord_id=ids["okra"])])])
    conn.execute("INSERT INTO hall_of_fame VALUES ('most_kills', 24, ?, 'KestrelNick', '4821', ?, "
                 "'2026-11-20T19:05:00+00:00', NULL, NULL)", (ids["kestrel"], HOME))
    conn.execute("INSERT INTO hall_of_fame VALUES ('most_avg_damage', 1600, ?, 'OTHERGUILDSECRETNAME', '1', ?, "
                 "'2026-11-20T19:05:00+00:00', NULL, NULL)", (ids["okra"], OTHER))
    conn.commit()
    return conn
