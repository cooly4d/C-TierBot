"""Read-only, guild-scoped queries for the website.

Every function takes an open `sqlite3.Connection` (the website's read-only one) and the guild to show, and never
opens a connection itself. Nothing here selects `users.access_token`. Discord IDs are returned for name lookups
and grouping inside the server and are never rendered.

A "saved finished queue" is a match that has a `queue_results` snapshot or `player_queue_stats` rows in the guild.
"""
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

from web_charts import game_count
from web_names import public_queue_id, safe_slug

# The running job's absolute deadline, set by the website's DB worker (web_server.WebDB) for its thread.
_job = threading.local()


def checkpoint():
    """Abort a job between units of work once its deadline has passed or the server is stopping. SQLite's own
    progress handler covers long statements; this covers loops of small queries and Python work."""
    deadline = getattr(_job, "deadline", None)
    stopping = getattr(_job, "stopping", None)
    if (deadline is not None and time.monotonic() > deadline) or (stopping is not None and stopping()):
        raise sqlite3.OperationalError("interrupted")


_ROW_COLUMNS = (
    "team_index, discord_id, display_name, username, slug, kills, total_damage, avg_damage, games, impact, "
    "duration_ms, match_start_ms, match_end_ms, winning_team_index, team_round_wins"
)


def _row_dict(row) -> dict:
    keys = [k.strip() for k in _ROW_COLUMNS.split(",")]
    return dict(zip(keys, row))


def _parse_json_list(raw):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(value, list) and all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        return value
    return None


def _parse_snapshot(raw):
    try:
        snap = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return snap if isinstance(snap, dict) and isinstance(snap.get("games", []), list) else None


def _iso_to_ms(iso: str | None):
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


# --- Queue rosters --------------------------------------------------------------------------------------------

def _group_stats(rows: list[dict]) -> dict:
    """Series totals from one player's per-game rows."""
    impacts = [r["impact"] for r in rows if isinstance(r.get("impact"), (int, float))]
    damage = sum(int(r.get("damage") or 0) for r in rows)
    games = len(rows)
    return {
        "kills": sum(int(r.get("kills") or 0) for r in rows),
        "damage": damage,
        "games": games,
        "avg": damage / games if games else None,
        "impact": sum(impacts) / len(impacts) if impacts else None,
    }


def build_queue_roster(snapshot: dict) -> list[list[dict]]:
    """Each team's players with series totals computed from the snapshot's per-game rows.

    v2 snapshots group by (team, key): `slug:` for a logged-in account, `name:` for a logged-out player. v1
    snapshots group linked rows by (team, discord_id), and unlinked rows by (team, name). Either way, a group
    whose key appears twice on one team in some game can't be split into people, so each of its rows stays its
    own entry with empty series totals ("—"). Name-only players can't be told apart from one game to the next, so
    a name-only player who played more than one game is marked `combined_by_name`."""
    games = [g for g in snapshot.get("games") or [] if isinstance(g, dict)]
    v2 = snapshot.get("v", 1) >= 2
    team_count = 0
    for g in games:
        for r in g.get("players") or []:
            if isinstance(r, dict) and isinstance(r.get("team"), int):
                team_count = max(team_count, r["team"] + 1)

    def row_key(r):
        if v2:
            return r.get("key") or f"name:{r.get('name')}"
        return ("id", r["discord_id"]) if r.get("discord_id") is not None else ("name", r.get("name"))

    ambiguous: set = set()
    for g in games:
        seen: dict = {}
        for r in g.get("players") or []:
            if isinstance(r, dict) and isinstance(r.get("team"), int):
                k = (r["team"], row_key(r))
                seen[k] = seen.get(k, 0) + 1
        ambiguous.update(k for k, count in seen.items() if count > 1)

    groups: dict = {}
    order: list = []
    for gi, g in enumerate(games):
        for ri, r in enumerate(g.get("players") or []):
            if not isinstance(r, dict) or not isinstance(r.get("team"), int):
                continue
            team = r["team"]
            person = row_key(r)
            name_only = not (str(person).startswith("slug:") if v2 else person[0] == "id")
            ambiguous_row = (team, person) in ambiguous
            if ambiguous_row:
                # One key twice on one team in some game: each observation stays its own row, no series totals.
                person = ("ambiguous", gi, ri)
            gkey = (team, person)
            if gkey not in groups:
                groups[gkey] = {"team": team, "rows": [], "name_only": name_only, "person": person,
                                "ambiguous": ambiguous_row}
                order.append(gkey)
            groups[gkey]["rows"].append(r)

    total_games = len(games)
    teams: list[list[dict]] = [[] for _ in range(team_count)]
    for gkey in order:
        group = groups[gkey]
        rows = group["rows"]
        last = rows[-1]
        discord_id = None if group["name_only"] else last.get("discord_id")
        is_ambiguous = group["ambiguous"]
        stats = None if is_ambiguous else _group_stats(rows)
        played = len(rows)
        teams[group["team"]].append({
            "person": group["person"],
            "name_only": group["name_only"],
            "discord_id": discord_id,
            "stored_name": last.get("name"),
            "slug": last.get("slug") if v2 and not group["name_only"] else None,
            "linked": discord_id is not None,
            "stats": stats,
            "played": played,
            "partial": f"{played}/{total_games}" if 0 < played < total_games and not is_ambiguous else None,
            "combined_by_name": group["name_only"] and played > 1 and not is_ambiguous,
        })
    return teams


def roster_from_rows(rows: list[dict]) -> list[list[dict]]:
    """Each team's players from stat rows (queues without a snapshot)."""
    team_count = max((r["team_index"] for r in rows), default=-1) + 1
    teams: list[list[dict]] = [[] for _ in range(team_count)]
    for r in rows:
        games = game_count(r)
        teams[r["team_index"]].append({
            "discord_id": r.get("discord_id"),
            "stored_name": r.get("display_name") or r.get("username"),
            "slug": None,
            "linked": r.get("discord_id") is not None,
            "stats": {
                "kills": r.get("kills"),
                "damage": r.get("total_damage"),
                "games": games,
                "avg": r.get("avg_damage") if games is not None else None,
                "impact": r.get("impact"),
            },
            "played": games,
            "partial": None,
            "combined_by_name": False,
        })
    return teams


def pick_mvp(teams: list[list[dict]], total_games: int, final_with_winner: bool):
    """(team, index) of the series MVP, as on the result card: the best impact (rounded as shown) among players
    who played every game. None when nobody qualifies, the best score is shared, or the series has no winner."""
    if not final_with_winner or not total_games or len(teams) > 2:
        return None
    # As image_utils.pick_mvp: a person on both sides counts once (games added up, impact averaged over their
    # scored games); the tag goes on their main row (the one with the most games).
    people: dict = {}
    for t, team in enumerate(teams):
        for i, p in enumerate(team):
            stats = p.get("stats")
            if not stats:
                continue
            # Only a Discord ID or an account slug is the same person on both sides; a name-only player is one
            # person per team, as the card's _player_key keeps unverified entries apart.
            key = p.get("person", (t, i))
            if p.get("name_only"):
                key = ("team", t, key)
            person = people.setdefault(key, {"games": 0, "points": 0.0, "scored": 0, "row": None})
            games = stats.get("games") or 0
            person["games"] += games
            if stats.get("impact") is not None and games:
                person["points"] += stats["impact"] * games
                person["scored"] += games
            if person["row"] is None or games > person["row"][0]:
                person["row"] = (games, (t, i))
    scored = [(int(p["points"] / p["scored"] + 0.5), p["row"][1]) for p in people.values()
              if p["scored"] and p["games"] >= total_games]
    if not scored:
        return None
    best = max(s for s, _ in scored)
    winners = [pos for s, pos in scored if s == best]
    return winners[0] if len(winners) == 1 else None


# --- Queues ----------------------------------------------------------------------------------------------------

def _queue_rows(conn, guild_id: int, match_id: str) -> list[dict]:
    return [_row_dict(r) for r in conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM player_queue_stats WHERE guild_id = ? AND match_id = ? "
        "ORDER BY team_index, rowid",
        (guild_id, str(match_id)),
    )]


def _queue_snapshot(conn, guild_id: int, match_id: str):
    row = conn.execute(
        "SELECT snapshot, saved_at FROM queue_results WHERE guild_id = ? AND match_id = ?",
        (guild_id, str(match_id)),
    ).fetchone()
    if row is None:
        return None, None
    return _parse_snapshot(row[0]), row[1]


def _queue_meta(rows: list[dict], snapshot, saved_at) -> dict:
    end_ms = max((r["match_end_ms"] for r in rows if r.get("match_end_ms")), default=None)
    start_ms = min((r["match_start_ms"] for r in rows if r.get("match_start_ms")), default=None)
    duration_ms = max((r["duration_ms"] for r in rows if r.get("duration_ms")), default=None)
    if snapshot is not None:
        # The snapshot is the queue's authoritative result, an unknown winner included; stat rows can be stale
        # (a rerun replaces rows per player and keeps participants it no longer sees).
        winner = snapshot.get("winning_team_index")
        raw_score = snapshot.get("team_round_wins")
        score = raw_score if isinstance(raw_score, list) and raw_score and all(
            isinstance(v, int) and not isinstance(v, bool) for v in raw_score) else None
    else:
        winner = next((r["winning_team_index"] for r in rows if r.get("winning_team_index") is not None), None)
        score = next((s for s in (_parse_json_list(r.get("team_round_wins")) for r in rows) if s), None)
    if not isinstance(winner, int) or isinstance(winner, bool) or winner < 0:
        winner = None
    return {
        "date_ms": end_ms or start_ms or _iso_to_ms(saved_at),
        "date_is_saved": end_ms is None and start_ms is None,
        "duration_ms": duration_ms,
        "winner": winner,
        "score": score,
    }


def get_queue(conn, guild_id: int, match_id: str):
    """Everything the queue page shows, or None when the guild has no saved finished queue by that number."""
    rows = _queue_rows(conn, guild_id, match_id)
    snapshot, saved_at = _queue_snapshot(conn, guild_id, match_id)
    if not rows and snapshot is None:
        return None
    meta = _queue_meta(rows, snapshot, saved_at)
    games = []
    if snapshot is not None:
        teams = build_queue_roster(snapshot)
        for index, g in enumerate(snapshot.get("games") or []):
            if not isinstance(g, dict):
                continue
            winner = g.get("winner")
            per_team: dict[int, list] = {}
            for r in g.get("players") or []:
                if not isinstance(r, dict) or not isinstance(r.get("team"), int):
                    continue
                per_team.setdefault(r["team"], []).append({
                    "discord_id": r.get("discord_id"),
                    "stored_name": r.get("name"),
                    "linked": r.get("discord_id") is not None,
                    "kills": r.get("kills"),
                    "damage": r.get("damage"),
                    "impact": r.get("impact"),
                })
            number = g.get("number")
            games.append({
                "number": number if isinstance(number, int) and number > 0 else index + 1,
                "winner": winner if isinstance(winner, int) and not isinstance(winner, bool) and winner >= 0 else None,
                "teams": [per_team.get(t, []) for t in range(max(len(teams), max(per_team, default=-1) + 1))],
            })
    else:
        teams = roster_from_rows(rows)
    total_games = len(games) if games else None
    if total_games is None and meta["score"]:
        total_games = sum(meta["score"])
    mvp = pick_mvp(teams, len(games), snapshot is not None and meta["winner"] is not None)
    return {
        "match_id": str(match_id),
        "has_snapshot": snapshot is not None,
        "teams": teams,
        "games": games,
        "total_games": total_games,
        "mvp": mvp,
        **meta,
    }


# A queue number the site can show and link: 1-10 ASCII digits, as web_names.public_queue_id and /queues/<n>.
# SQLite's length() and GLOB stop at an embedded NUL, so the byte length must also equal the character length
# (true only for text with no NUL, and digits are one byte each); list_queue_ids validates again in Python.
PUBLIC_ID_SQL = ("(typeof(match_id) = 'text' AND length(match_id) BETWEEN 1 AND 10 "
                 "AND length(CAST(match_id AS BLOB)) = length(match_id) AND match_id NOT GLOB '*[^0-9]*')")


def count_queues(conn, guild_id: int) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM (SELECT match_id FROM player_queue_stats WHERE guild_id = ? AND {PUBLIC_ID_SQL} "
        f"UNION SELECT match_id FROM queue_results WHERE guild_id = ? AND {PUBLIC_ID_SQL})",
        (guild_id, guild_id),
    ).fetchone()[0]


def list_queue_ids(conn, guild_id: int, limit: int, offset: int) -> list[str]:
    """Saved finished queues of the guild with a public queue number, newest first (stat-row end time, else the
    snapshot's saved time)."""
    rows = conn.execute(
        """
        WITH r AS (
            SELECT match_id, MAX(COALESCE(match_end_ms, match_start_ms)) AS end_ms
            FROM player_queue_stats WHERE guild_id = ? AND {public} GROUP BY match_id
        ),
        q AS (SELECT match_id, saved_at FROM queue_results WHERE guild_id = ? AND {public}),
        ids AS (SELECT match_id FROM r UNION SELECT match_id FROM q)
        SELECT ids.match_id,
               COALESCE(r.end_ms, CAST(strftime('%s', q.saved_at) AS INTEGER) * 1000, 0) AS sort_ms
        FROM ids LEFT JOIN r ON r.match_id = ids.match_id LEFT JOIN q ON q.match_id = ids.match_id
        ORDER BY sort_ms DESC, CAST(ids.match_id AS INTEGER) DESC, ids.match_id DESC
        LIMIT ? OFFSET ?
        """.format(public=PUBLIC_ID_SQL),
        (guild_id, guild_id, limit, offset),
    ).fetchall()
    return [mid for mid in (public_queue_id(r[0]) for r in rows) if mid is not None]


def list_queues(conn, guild_id: int, limit: int, offset: int) -> list[dict]:
    out = []
    for match_id in list_queue_ids(conn, guild_id, limit, offset):
        checkpoint()
        queue = get_queue(conn, guild_id, match_id)
        if queue is not None:
            queue.pop("games", None)
            out.append(queue)
    return out


# --- Players -----------------------------------------------------------------------------------------------------

def resolve_player_slug(conn, guild_id: int, slug: str):
    """("page", discord_id), ("redirect", discord_id) for an account the user played on before re-running /verify
    that nobody holds now, or None. Only users with main-guild stat rows have a page."""
    if not safe_slug(slug):
        return None
    candidates = conn.execute(
        """
        SELECT u.discord_id, COUNT(DISTINCT p.match_id) AS queues
        FROM users u JOIN player_queue_stats p ON p.discord_id = u.discord_id AND p.guild_id = ?
        WHERE u.slug = ?
        GROUP BY u.discord_id
        ORDER BY queues DESC, u.discord_id ASC
        """,
        (guild_id, slug),
    ).fetchall()
    if candidates:
        return ("page", candidates[0][0])
    if conn.execute("SELECT 1 FROM users WHERE slug = ?", (slug,)).fetchone():
        # The account's current holder has no page yet (say the operator freed it and someone else relinked it):
        # never send its URL to whoever held it before.
        return None
    owners = conn.execute(
        "SELECT DISTINCT discord_id FROM player_queue_stats WHERE guild_id = ? AND slug = ? AND discord_id IS NOT NULL",
        (guild_id, slug),
    ).fetchall()
    if len(owners) == 1:
        return ("redirect", owners[0][0])
    return None


def player_slugs(conn, guild_id: int, discord_ids) -> dict:
    """{discord_id: slug} for every user whose page URL may be shown: their current slug passes the filter and
    resolves back to them. Everyone else gets no link."""
    ids = sorted({int(i) for i in discord_ids if i is not None})
    if not ids:
        return {}
    found = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for discord_id, slug in conn.execute(
            f"SELECT discord_id, slug FROM users WHERE discord_id IN ({marks})", chunk
        ):
            found[discord_id] = slug
    result, cache = {}, {}
    for discord_id, slug in found.items():
        if not safe_slug(slug):
            continue
        if slug not in cache:
            cache[slug] = resolve_player_slug(conn, guild_id, slug)
        if cache[slug] == ("page", discord_id):
            result[discord_id] = slug
    return result


def stored_names(conn, guild_id: int, discord_ids) -> dict:
    """{discord_id: name}: the fallback name for a linked player, newest main-guild source first: their newest
    stat row's display_name, else their row in the newest snapshot that has one, else their Hall of Fame record."""
    ids = sorted({int(i) for i in discord_ids if i is not None})
    out: dict = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        for discord_id, name in conn.execute(
            f"""
            SELECT discord_id, display_name FROM player_queue_stats
            WHERE guild_id = ? AND discord_id IN ({marks}) AND display_name IS NOT NULL AND display_name != ''
            ORDER BY COALESCE(match_end_ms, match_start_ms, 0) DESC, CAST(match_id AS INTEGER) DESC
            """,
            (guild_id, *chunk),
        ):
            out.setdefault(discord_id, name)
    for discord_id in ids:
        if discord_id in out:
            continue
        checkpoint()
        name = _snapshot_name(conn, guild_id, discord_id)
        if name is None:
            row = conn.execute(
                "SELECT display_name FROM hall_of_fame WHERE guild_id = ? AND discord_id = ? "
                "AND display_name IS NOT NULL AND display_name != '' ORDER BY achieved_at DESC LIMIT 1",
                (guild_id, discord_id),
            ).fetchone()
            name = row[0] if row else None
        if name:
            out[discord_id] = name
    return out


def _snapshot_name(conn, guild_id: int, discord_id: int):
    """The user's name in the newest main-guild snapshot that has them (its latest game first)."""
    for (raw,) in conn.execute(
        "SELECT snapshot FROM queue_results WHERE guild_id = ? AND instr(snapshot, ?) > 0 "
        "ORDER BY saved_at DESC, match_id DESC",
        (guild_id, str(discord_id)),
    ):
        checkpoint()
        snap = _parse_snapshot(raw)
        for g in reversed((snap or {}).get("games") or []):
            for r in (g.get("players") or []) if isinstance(g, dict) else []:
                if isinstance(r, dict) and r.get("discord_id") == discord_id and r.get("name"):
                    return r["name"]
    return None


_GAMES_EXPR = (
    "CASE WHEN games IS NOT NULL AND games > 0 THEN games "
    "WHEN avg_damage > 0 THEN total_damage / avg_damage ELSE 0 END"
)
_IMPACT_GAMES_EXPR = (
    "CASE WHEN impact IS NOT NULL THEN (CASE WHEN games IS NOT NULL AND games > 0 THEN games "
    "WHEN avg_damage > 0 THEN total_damage / avg_damage ELSE 1 END) ELSE 0 END"
)
_IMPACT_POINTS_EXPR = (
    "CASE WHEN impact IS NOT NULL THEN impact * (CASE WHEN games IS NOT NULL AND games > 0 THEN games "
    "WHEN avg_damage > 0 THEN total_damage / avg_damage ELSE 1 END) ELSE 0 END"
)


def get_player_summary(conn, guild_id: int, discord_id: int, now: datetime | None = None) -> dict:
    """Main-server twin of db.get_player_queue_stats_summary (same formulas, one guild)."""
    now = now or datetime.now(timezone.utc)
    month_start_ms = int(datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
    all_time = conn.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(duration_ms), 0), COALESCE(SUM(total_damage), 0),
               COALESCE(SUM({_GAMES_EXPR}), 0), COALESCE(SUM({_IMPACT_POINTS_EXPR}), 0),
               COALESCE(SUM({_IMPACT_GAMES_EXPR}), 0)
        FROM player_queue_stats WHERE guild_id = ? AND discord_id = ?
        """,
        (guild_id, discord_id),
    ).fetchone()
    monthly = conn.execute(
        f"""
        SELECT COALESCE(SUM(total_damage), 0), COALESCE(SUM({_GAMES_EXPR}), 0),
               COALESCE(SUM({_IMPACT_POINTS_EXPR}), 0), COALESCE(SUM({_IMPACT_GAMES_EXPR}), 0)
        FROM player_queue_stats WHERE guild_id = ? AND discord_id = ? AND match_end_ms >= ?
        """,
        (guild_id, discord_id, month_start_ms),
    ).fetchone()
    a_games, a_imp_games = float(all_time[3] or 0), float(all_time[5] or 0)
    m_games, m_imp_games = float(monthly[1] or 0), float(monthly[3] or 0)
    return {
        "matches": int(all_time[0] or 0),
        "total_duration_ms": int(all_time[1] or 0),
        "all_time_avg_damage": float(all_time[2] or 0) / a_games if a_games > 0 else None,
        "monthly_avg_damage": float(monthly[0] or 0) / m_games if m_games > 0 else None,
        "all_time_avg_impact": float(all_time[4] or 0) / a_imp_games if a_imp_games > 0 else None,
        "monthly_avg_impact": float(monthly[2] or 0) / m_imp_games if m_imp_games > 0 else None,
    }


def count_player_queues(conn, guild_id: int, discord_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM player_queue_stats WHERE guild_id = ? AND discord_id = ?", (guild_id, discord_id)
    ).fetchone()[0]


def get_player_history(conn, guild_id: int, discord_id: int, limit: int, offset: int = 0) -> list[dict]:
    """A user's main-guild queues with saved stats, newest first. Legacy rows missing kills or impact are filled
    from the queue's snapshot only on a safe match: by discord_id when the snapshot row has one, or by name only
    when the snapshot row has no discord_id and that name is unique on that team in that game. `match_id` is None
    for a stored number the site can't show (the row's stats still count, as in /profile)."""
    rows = conn.execute(
        """
        SELECT match_id, team_index, total_damage, avg_damage, games, duration_ms, match_start_ms, match_end_ms,
               display_name, username, kills, impact, winning_team_index, team_round_wins
        FROM player_queue_stats WHERE guild_id = ? AND discord_id = ?
        ORDER BY COALESCE(match_end_ms, match_start_ms, 0) DESC, CAST(match_id AS INTEGER) DESC, match_id DESC
        LIMIT ? OFFSET ?
        """,
        (guild_id, discord_id, limit, offset),
    ).fetchall()
    out = []
    for r in rows:
        checkpoint()
        raw_id = str(r[0])
        item = {
            "match_id": public_queue_id(raw_id), "team_index": int(r[1]), "total_damage": r[2], "avg_damage": r[3],
            "games": r[4], "duration_ms": r[5], "match_start_ms": r[6], "match_end_ms": r[7],
            "display_name": r[8], "username": r[9], "kills": r[10], "impact": r[11],
            "winning_team_index": r[12], "team_round_wins": _parse_json_list(r[13]),
        }
        if item["kills"] is None or item["impact"] is None or item["winning_team_index"] is None \
                or item["team_round_wins"] is None:
            snapshot, _ = _queue_snapshot(conn, guild_id, raw_id)
            if snapshot:
                _enrich_from_snapshot(item, snapshot, discord_id)
        winner = item["winning_team_index"]
        item["won"] = (winner == item["team_index"]) if isinstance(winner, int) and winner >= 0 else None
        score = item["team_round_wins"]
        if score and 0 <= item["team_index"] < len(score) and len(score) == 2:
            mine, theirs = score[item["team_index"]], score[1 - item["team_index"]]
            item["score"] = f"{mine}-{theirs}"
        else:
            item["score"] = None
        out.append(item)
    return out


def _enrich_from_snapshot(item: dict, snapshot: dict, discord_id: int):
    if item["winning_team_index"] is None and isinstance(snapshot.get("winning_team_index"), int):
        item["winning_team_index"] = snapshot["winning_team_index"]
    if item["team_round_wins"] is None and isinstance(snapshot.get("team_round_wins"), list):
        item["team_round_wins"] = [int(w) for w in snapshot["team_round_wins"] if isinstance(w, int)]
    if item["kills"] is not None and item["impact"] is not None:
        return
    names = {n for n in (item["display_name"], item["username"]) if n}
    kills, impacts, matched, unresolved = 0, [], 0, 0
    for g in snapshot.get("games") or []:
        if not isinstance(g, dict):
            continue
        players = [p for p in (g.get("players") or []) if isinstance(p, dict)]
        # Resolve the player once per game: their exact Discord ID anywhere in the game (they may have switched
        # sides), else a name unique on their team among unlinked rows. Anything ambiguous is unresolved.
        by_id = [p for p in players if p.get("discord_id") == discord_id]
        if len(by_id) > 1:
            unresolved += 1
            continue
        if by_id:
            chosen = by_id[0]
        else:
            candidates = [p for p in players if p.get("team") == item["team_index"] and p.get("discord_id") is None
                          and p.get("name") in names]
            if not candidates:
                continue  # not in this game (a substitute)
            twins = [p for p in players if p.get("team") == item["team_index"]
                     and p.get("name") == candidates[0].get("name")]
            if len(candidates) != 1 or len(twins) != 1:
                unresolved += 1
                continue
            chosen = candidates[0]
        matched += 1
        kills += int(chosen.get("kills") or 0)
        if isinstance(chosen.get("impact"), (int, float)):
            impacts.append(float(chosen["impact"]))
    # Fill only from a complete attribution: every game resolved, and as many as the row says they played. A
    # partial total would turn into a precise but wrong per-game value.
    expected = game_count(item)
    if not matched or unresolved or (expected is not None and matched != expected):
        return
    if item["kills"] is None:
        item["kills"] = kills
    if item["impact"] is None and impacts:
        item["impact"] = sum(impacts) / len(impacts)


# --- Hall of Fame -------------------------------------------------------------------------------------------------

def queue_exists(conn, guild_id: int, match_id) -> bool:
    """Whether /queues/<match_id> has a page: the guild has stat rows or a readable snapshot for it."""
    if match_id is None:
        return False
    if conn.execute("SELECT 1 FROM player_queue_stats WHERE guild_id = ? AND match_id = ? LIMIT 1",
                    (guild_id, str(match_id))).fetchone():
        return True
    return _queue_snapshot(conn, guild_id, match_id)[0] is not None


def get_hall_of_fame(conn, guild_id: int) -> dict:
    """{record_type: record} for records whose stored guild is this guild. `has_page` says whether the record's
    queue still has a page to link to (a record can outlive its queue's rows and snapshot)."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hall_of_fame)")}
    game_col = "game_number" if "game_number" in columns else "NULL"
    out = {}
    for row in conn.execute(
        f"SELECT record_type, value, discord_id, display_name, match_id, guild_id, achieved_at, duration_ms, "
        f"{game_col} FROM hall_of_fame WHERE guild_id = ?",
        (guild_id,),
    ):
        match_id = public_queue_id(row[4])
        out[row[0]] = {
            "value": row[1], "discord_id": row[2], "stored_name": row[3], "match_id": match_id,
            "achieved_at": row[6], "duration_ms": row[7], "game_number": row[8],
        }
    for rec in out.values():
        checkpoint()
        rec["has_page"] = queue_exists(conn, guild_id, rec["match_id"])
    return out


def earliest_month(conn, guild_id: int):
    row = conn.execute(
        "SELECT MIN(match_end_ms) FROM player_queue_stats WHERE guild_id = ? AND match_end_ms IS NOT NULL",
        (guild_id,),
    ).fetchone()
    if not row or row[0] is None:
        return None
    dt = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
    return dt.year, dt.month
