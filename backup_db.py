"""Daily SQLite backup, run by a Coolify scheduled task inside the bot's container: python backup_db.py

Uses SQLite's online backup API, so the copy is consistent even while the bot is writing. Copies go to
backups/ next to the database (on the persistent volume, /app/data/backups in the container) and only the
newest BACKUP_KEEP (default 14) are kept. This protects against a bad write or a mistaken `sql` command,
not against losing the VPS disk: for that, use Hostinger's VPS backups or download a copy now and then.
"""
import glob
import os
import sqlite3
import sys
from datetime import datetime, timezone

from db import DB_PATH

BACKUP_PREFIX = "leaderboard-"


def backup_database(db_path: str, backup_dir: str, keep: int, now: datetime | None = None) -> str:
    os.makedirs(backup_dir, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"{BACKUP_PREFIX}{stamp}.db")
    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    backups = sorted(glob.glob(os.path.join(backup_dir, f"{BACKUP_PREFIX}*.db")))
    for old in backups[:-keep] if keep > 0 else []:
        os.remove(old)
    return target


def main() -> int:
    backup_dir = os.getenv("BACKUP_DIR") or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
    keep = int(os.getenv("BACKUP_KEEP", "14"))
    target = backup_database(DB_PATH, backup_dir, keep)
    print(f"Backed up {DB_PATH} to {target} ({os.path.getsize(target)} bytes); keeping the newest {keep}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
