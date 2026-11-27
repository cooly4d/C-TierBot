"""The operator panel's read-only SQL, run in a short-lived process of its own.

console_service.PanelConsole starts `python -I panel_sql.py` for each panel `sql` command, writes the request as JSON
on stdin and reads the console text from stdout. A separate process can be killed outright when its time is up, and on
Linux it also runs under CPU-time, memory and file-size limits, so a query can't tie up the bot's threads or memory.
The process gets no secrets in its environment.

Standard library only: the bot's own modules aren't importable here (importing db would create tables).
"""
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

SECONDS = 2.0        # the website's own job limit, so a query holds the database no longer than a page
MAX_ROWS = 200
MAX_CELL = 300
MAX_CHARS = 150_000
MAX_OUTPUT = 500_000  # characters of reply, at most 2 MB of UTF-8: well under what the website reads back
MAX_REQUEST = 64 * 1024

# What the panel can read: the bot's tables and columns (db.py), except the per-user OAuth token. A column that
# isn't listed (one added later) reads as NULL with a note until it's added here; a table that isn't listed (a copy
# made in the SSH console, a view, a virtual table) is refused. tests/test_web_console.py fails when db.py's schema
# has a column that's neither readable nor hidden.
READABLE = {
    "guild_settings": {"guild_id", "channel_id", "last_updated"},
    "hall_of_fame": {"record_type", "value", "discord_id", "display_name", "match_id", "guild_id", "achieved_at",
                     "duration_ms", "game_number"},
    "player_queue_stats": {"guild_id", "match_id", "player_key", "discord_id", "display_name", "username", "slug",
                           "team_index", "total_damage", "avg_damage", "games", "duration_ms", "match_start_ms",
                           "match_end_ms", "cached_at", "kills", "impact", "winning_team_index", "team_round_wins"},
    "processed_matches": {"guild_id", "match_id", "processed_at"},
    "queue_results": {"guild_id", "match_id", "snapshot", "saved_at"},
    "users": {"discord_id", "slug", "username"},
    # The schema itself (its CREATE statements), and the empty temp schema.
    "sqlite_master": {"type", "name", "tbl_name", "rootpage", "sql"},
    "sqlite_schema": {"type", "name", "tbl_name", "rootpage", "sql"},
    "sqlite_temp_master": {"type", "name", "tbl_name", "rootpage", "sql"},
    "sqlite_temp_schema": {"type", "name", "tbl_name", "rootpage", "sql"},
}
HIDDEN = {("users", "access_token")}
# A table's implicit row ID. A column declared with one of these names isn't the row ID: it needs listing like any
# other column.
ROWID_NAMES = {"rowid", "_rowid_", "oid"}
# Table-valued functions that read only their arguments (json_each, json_tree) or the schema (the pragma ones).
READ_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list",
                "table_list"}
VALUE_TABLES = {"json_each", "json_tree"} | {f"pragma_{name}" for name in READ_PRAGMAS}
# (A table or view in the database with one of these names takes the name over: it's then refused like any other
# unlisted table.)
# SQLite's built-in SQL functions. Anything else (load_extension, readfile, fts3_tokenizer, an extension's
# function) is refused.
FUNCTIONS = {
    # core
    "abs", "char", "coalesce", "concat", "concat_ws", "format", "glob", "hex", "if", "ifnull", "iif", "instr",
    "last_insert_rowid", "length", "like", "likelihood", "likely", "lower", "ltrim", "max", "min", "nullif",
    "octet_length", "printf", "quote", "random", "randomblob", "replace", "round", "rtrim", "sign", "soundex",
    "sqlite_version", "substr", "substring", "trim", "typeof", "unhex", "unicode", "unistr", "unistr_quote",
    "unlikely", "upper", "zeroblob", "changes", "total_changes",
    # aggregate and window
    "avg", "count", "group_concat", "string_agg", "sum", "total", "row_number", "rank", "dense_rank",
    "percent_rank", "cume_dist", "ntile", "lag", "lead", "first_value", "last_value", "nth_value",
    # date and time
    "date", "time", "datetime", "julianday", "unixepoch", "strftime", "timediff", "current_date", "current_time",
    "current_timestamp",
    # JSON
    "json", "jsonb", "json_array", "jsonb_array", "json_array_length", "json_error_position", "json_extract",
    "jsonb_extract", "json_insert", "jsonb_insert", "json_object", "jsonb_object", "json_patch", "jsonb_patch",
    "json_pretty", "json_quote", "json_remove", "jsonb_remove", "json_replace", "jsonb_replace", "json_set",
    "jsonb_set", "json_type", "json_valid", "json_group_array", "jsonb_group_array", "json_group_object",
    "jsonb_group_object",
    # math
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh", "ceil", "ceiling", "cos", "cosh", "degrees", "exp",
    "floor", "ln", "log", "log10", "log2", "mod", "pi", "pow", "power", "radians", "sin", "sinh", "sqrt", "tan",
    "tanh", "trunc",
}

# Hard limits for the process itself (Linux; the parent's kill at the time limit applies everywhere).
LIMIT_MEMORY = 1 << 30       # 1 GiB of address space
LIMIT_FILE = 256 << 20       # SQLite's temporary sort files
LIMIT_FILES_OPEN = 64


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """A simple ASCII table for terminal display (the SSH console's tables use it too)."""
    if not rows:
        return "(empty)"
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(str(cell)))

    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "-+-".join("-" * widths[i] for i in range(len(headers)))
    row_lines = [
        " | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return f"{header_line}\n{separator}\n" + "\n".join(row_lines)


def _cell(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    text = " ".join(str(value).split())  # a multi-line JSON value stays on its row
    return text if len(text) <= MAX_CELL else text[:MAX_CELL - 1] + "…"


# SQLite never shows the authorizer the columns that JOIN ... USING and NATURAL JOIN compare, so they could test a
# hidden column's value (or join through an unlisted table) unseen. The panel refuses both words outside quotes.
_IMPLICIT_JOIN = re.compile(r"\b(?:using|natural)\b", re.IGNORECASE)


def _bare_sql(query: str) -> str:
    """The query with string literals, quoted identifiers and comments blanked out, leaving only its own words."""
    out, i, n = [], 0, len(query)
    while i < n:
        ch = query[i]
        if ch in "'\"`":
            j = i + 1
            while j < n and not (query[j] == ch and not query.startswith(ch * 2, j)):
                j += 2 if query.startswith(ch * 2, j) else 1
            out.append(" ")
            i = j + 1
        elif ch == "[":
            j = query.find("]", i + 1)
            out.append(" ")
            i = n if j < 0 else j + 1
        elif query.startswith("--", i):
            j = query.find("\n", i)
            out.append(" ")
            i = n if j < 0 else j
        elif query.startswith("/*", i):
            j = query.find("*/", i + 2)
            out.append(" ")
            i = n if j < 0 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def run_readonly_sql(db_path: str, query: str, *, seconds: float = SECONDS, max_rows: int = MAX_ROWS) -> str:
    """One read-only statement on a read-only connection, returned as console text."""
    query = (query or "").strip()
    if not query:
        return "[Console Error] Usage: sql <query>\n"
    if _IMPLICIT_JOIN.search(_bare_sql(query)):
        return ("[Console SQL Error] JOIN ... USING and NATURAL JOIN aren't available in the panel's sql, because "
                "SQLite doesn't check the columns they compare. Join with ON a.column = b.column instead.\n")
    hidden: list[str] = []
    unlisted: list[str] = []
    refused: list[str] = []
    value_tables: set[str] = set()  # VALUE_TABLES that no table or view in the database has taken over
    declared: dict[str, set[str]] = {}  # the columns each readable table really has

    def authorizer(action, arg1, arg2, _db_name, _source):
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if column == "" or table in value_tables:
                # "" is a whole-table read, as in count(*) over a table or a CTE: a row count, never a value
                # (every column a query reads comes through here by name).
                return sqlite3.SQLITE_OK
            columns = READABLE.get(table)
            if columns is None:
                refused.append(f"table {arg1}")
                return sqlite3.SQLITE_DENY
            if (table, column) in HIDDEN:
                hidden.append(f"{table}.{column}")
                return sqlite3.SQLITE_IGNORE  # the column reads as NULL
            if column in columns or (column in ROWID_NAMES and column not in declared.get(table, ())):
                return sqlite3.SQLITE_OK
            unlisted.append(f"{table}.{column}")
            return sqlite3.SQLITE_IGNORE
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if (arg2 or "").lower() in FUNCTIONS:
                return sqlite3.SQLITE_OK
            refused.append(f"function {arg2}()")
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in READ_PRAGMAS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY  # every write, schema change, ATTACH, transaction and other PRAGMA

    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    deadline = time.monotonic() + seconds
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=0.25)
    except sqlite3.Error as exc:
        return f"[Console SQL Error] Could not open the database: {exc}\n"
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)  # no gigabyte randomblob()
        conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.setlimit(sqlite3.SQLITE_LIMIT_WORKER_THREADS, 0)
        # Before the authorizer: what the names mean in this database.
        names = {str(name).lower() for (name,) in conn.execute("SELECT name FROM sqlite_master")}
        value_tables.update(VALUE_TABLES - names)
        for table in READABLE.keys() & names:  # the PRAGMA statement, which no table or view can take over
            declared[table] = {str(row[1]).lower() for row in conn.execute(f'PRAGMA table_info("{table}")')}
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        cursor = conn.execute(query)
        if cursor.description is None:
            return "[Console] Done. The statement returned no rows.\n"
        headers = [d[0] for d in cursor.description]
        rows, chars, more = [], 0, False
        for row in cursor:
            if len(rows) >= max_rows or chars > MAX_CHARS:
                more = True
                break
            cells = [_cell(v) for v in row]
            chars += sum(len(c) for c in cells)
            rows.append(cells)
    except sqlite3.DatabaseError as exc:
        text = str(exc).lower()
        if refused:
            return (f"[Console SQL Error] The panel's sql can't use {refused[0]}: it reads the bot's own tables "
                    "with SQLite's built-in functions. Use the SSH console for anything else.\n")
        if any(word in text for word in ("not authorized", "authorization denied", "prohibited", "readonly",
                                         "read-only")):
            return "[Console SQL Error] Not allowed here: the panel's sql only reads. Change data in the SSH console.\n"
        if "interrupt" in text:
            return f"[Console SQL Error] Stopped after {seconds:g} s, the panel's limit for one query.\n"
        if "locked" in text or "busy" in text:
            return "[Console SQL Error] The bot is writing to the database right now. Try again.\n"
        if "full" in text or "out of memory" in text:
            return "[Console SQL Error] The query needed more memory or temporary disk than the panel allows.\n"
        return f"[Console SQL Error] {exc}\n"
    except (sqlite3.Error, ValueError, MemoryError) as exc:  # ProgrammingError: more than one statement
        if isinstance(exc, MemoryError):
            return "[Console SQL Error] The query needed more memory or temporary disk than the panel allows.\n"
        return f"[Console SQL Error] {exc}\n"
    finally:
        conn.close()
    if not rows:
        out = "[Console] Query returned 0 rows.\n"
    else:
        count = f"first {len(rows)} rows shown" if more else f"{len(rows)} row{'s' if len(rows) != 1 else ''}"
        table = format_table(headers, rows)
        if len(table) > MAX_OUTPUT:  # wide padded columns or long column names
            table = table[:MAX_OUTPUT].rsplit("\n", 1)[0]
            count += f", cut at {MAX_OUTPUT:,} characters: select fewer or narrower columns"
        out = f"{table}\n({count})\n"
    for name in dict.fromkeys(hidden):
        out += f"[Console Warning] {name} is hidden in the panel and reads as NULL.\n"
    for name in dict.fromkeys(unlisted):
        out += f"[Console Warning] {name} isn't on the panel's list of readable columns, so it reads as NULL.\n"
    return out


class LimitError(RuntimeError):
    """A resource limit couldn't be set, so the query must not run."""


def limit_resources(seconds: float):
    """CPU time, address space, file size and open files for this process (Linux; elsewhere nothing). A stricter
    limit already in place is kept. Raises LimitError when one can't be set."""
    try:
        import resource
    except ImportError:
        return
    infinity = resource.RLIM_INFINITY
    for name, value in (("RLIMIT_CPU", int(seconds) + 2), ("RLIMIT_AS", LIMIT_MEMORY),
                        ("RLIMIT_FSIZE", LIMIT_FILE), ("RLIMIT_NOFILE", LIMIT_FILES_OPEN)):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = resource.getrlimit(which)
            for limit in (soft, hard):
                if limit != infinity:
                    value = min(value, limit)
            resource.setrlimit(which, (value, value if hard == infinity else hard))
        except (ValueError, OSError) as exc:
            raise LimitError(name) from exc


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(raw) > MAX_REQUEST:
        text = "[Console SQL Error] The query is too long.\n"
    else:
        request = json.loads(raw.decode("utf-8"))
        seconds = float(request.get("seconds", SECONDS))
        try:
            limit_resources(seconds)
        except LimitError as exc:
            text = (f"[Console SQL Error] The query didn't run: the panel couldn't set its {exc} limit. "
                    "Use the SSH console.\n")
        else:
            text = run_readonly_sql(request["db"], request["query"], seconds=seconds,
                                    max_rows=int(request.get("max_rows", MAX_ROWS)))
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
