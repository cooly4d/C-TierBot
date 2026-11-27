"""Export a token-free copy of the bot's database for the local website preview.

Piped into the running container (tools/ isn't in the image):
    docker exec -i <container> python - < tools/export_preview_db.py > preview.db.part

It reads the live DB read-only inside one read transaction, writes a new file in a private temp directory (never on
the data volume) holding only the tables the website needs, with `users.access_token` blanked, checks that no real
token reached the file, streams it to stdout, prints row counts (never data) to stderr, and always deletes its temp
directory. Exit code 0 means the stream is complete and verified.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

TABLES = {
    "guild_settings": ["guild_id", "channel_id", "last_updated"],
    "processed_matches": ["guild_id", "match_id", "processed_at"],
    "hall_of_fame": ["record_type", "value", "discord_id", "display_name", "match_id", "guild_id", "achieved_at",
                     "duration_ms", "game_number"],
    "player_queue_stats": ["guild_id", "match_id", "player_key", "discord_id", "display_name", "username", "slug",
                           "team_index", "total_damage", "avg_damage", "games", "duration_ms", "match_start_ms",
                           "match_end_ms", "cached_at", "kills", "impact", "winning_team_index", "team_round_wins"],
    "queue_results": ["guild_id", "match_id", "snapshot", "saved_at"],
}


def export(source: str, out_dir: str) -> tuple[str, dict, list[str]]:
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    target = os.path.join(out_dir, "preview.db")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    dst = sqlite3.connect(target)
    counts = {}
    try:
        src.execute("BEGIN")
        tokens = [row[0] for row in src.execute("SELECT access_token FROM users") if row[0]]
        schema = {name: sql for name, sql in src.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")}
        for table, wanted in TABLES.items():
            if table not in schema:
                continue
            have = {row[1] for row in src.execute(f"PRAGMA table_info({table})")}
            cols = [c for c in wanted if c in have]
            dst.execute(schema[table])
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            dst.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
            counts[table] = len(rows)
        dst.execute(schema["users"])
        users = src.execute("SELECT discord_id, slug, username FROM users").fetchall()
        dst.executemany("INSERT INTO users (discord_id, access_token, slug, username) VALUES (?, '', ?, ?)", users)
        counts["users"] = len(users)
        src.execute("COMMIT")
        dst.commit()
        leaked = dst.execute("SELECT COUNT(*) FROM users WHERE access_token != ''").fetchone()[0]
        if leaked:
            raise RuntimeError("a token value remained in users")
    finally:
        src.close()
        dst.close()
    return target, counts, tokens


def main() -> int:
    source = os.environ.get("DB_PATH") or "/app/data/leaderboard.db"
    out_dir = tempfile.mkdtemp(prefix="ctier-preview-")
    os.chmod(out_dir, 0o700)
    try:
        target, counts, tokens = export(source, out_dir)
        data = Path(target).read_bytes()
        for token in tokens:
            if token.encode() in data:
                print("Export refused: a token was found in the file.", file=sys.stderr)
                return 3
        for table, count in sorted(counts.items()):
            print(f"{table}: {count}", file=sys.stderr)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        print(f"Export failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
