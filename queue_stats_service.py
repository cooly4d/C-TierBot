"""Ties NeatQueue match calculation to hall-of-fame record tracking, the /queue_stats
result image, and per-command JSON logging.
"""
import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone

import discord

from bot_config import (
    HALL_OF_FAME_RECORDS,
    MIN_GAMES_FOR_HALL_OF_FAME_UPDATE,
    QUEUE_STATS_LOG_DIR,
    QUEUE_STATS_LOG_MAX_FILES,
    bot,
)
from db import (
    QUEUE_RESULT_RETENTION_DAYS,
    best_game_damage,
    cache_player_queue_stats,
    clear_hall_of_fame_records,
    get_hall_of_fame_record,
    get_queue_result,
    save_queue_result,
    try_set_hall_of_fame_record,
)
from image_utils import (
    format_duration_ms,
    generate_queue_game_image,
    generate_queue_result_image,
    has_impact_scores,
)
from neatqueue_client import calculate_queue_match_stats, player_identity, resolve_queue_user_display_names

log = logging.getLogger(__name__)
# 2: each game row carries its own key/username/slug and per-row linkage (see _snapshot_row).
QUEUE_SNAPSHOT_VERSION = 2
# The result image's filename carries the queue number, which is how the Game breakdown button knows which queue
# a result message belongs to (see parse_queue_match_id).
_QUEUE_RESULT_FILENAME = re.compile(r"^queue_stats_(.+)\.png$")
_QUEUE_RESULT_CONTENT = re.compile(r"NeatQueue#(\S+)")


def queue_result_filename(match_id: str) -> str:
    return f"queue_stats_{match_id}.png"


def parse_queue_match_id(message) -> str | None:
    """The queue number a result message is about, read from its image's filename (falling back to its text)."""
    if message is None:
        return None
    for attachment in getattr(message, "attachments", None) or []:
        found = _QUEUE_RESULT_FILENAME.match(getattr(attachment, "filename", "") or "")
        if found:
            return found.group(1)
    found = _QUEUE_RESULT_CONTENT.search(getattr(message, "content", "") or "")
    return found.group(1) if found else None


def prune_queue_stats_logs(log_dir: str | None = None, max_files: int | None = None) -> int:
    """Removes the oldest queue_stats_*.json diagnostic logs when the directory exceeds max_files."""
    target_dir = log_dir if log_dir is not None else QUEUE_STATS_LOG_DIR
    limit = QUEUE_STATS_LOG_MAX_FILES if max_files is None else max_files
    if limit is None or limit <= 0 or not os.path.isdir(target_dir):
        return 0

    try:
        names = os.listdir(target_dir)
    except OSError:
        return 0
    entries = sorted(name for name in names if name.startswith("queue_stats_") and name.endswith(".json"))
    # A .tmp left by a write that died midway is never listed; clean it up once it's clearly abandoned.
    stale_before = time.time() - 3600
    for name in names:
        if name.startswith("queue_stats_") and name.endswith(".json.tmp"):
            path = os.path.join(target_dir, name)
            try:
                if os.path.getmtime(path) < stale_before:
                    os.remove(path)
            except OSError:
                pass

    excess = len(entries) - limit
    if excess <= 0:
        return 0

    removed = 0
    for old_name in entries[:excess]:
        try:
            os.remove(os.path.join(target_dir, old_name))
            removed += 1
        except OSError:
            continue
    return removed


# What started a queue lookup, as recorded in its diagnostic log's "event".
QUEUE_LOG_TRIGGERS = (
    "queue_stats_command",
    "auto_post",
    "catch_up",
    "queue_channel_reply",
    "queue_prefix_command",
)


def write_queue_stats_log(
    match_id: str,
    match_result: dict | None,
    error_text: str | None,
    *,
    trigger: str,
    guild_id: int | None,
    channel_id: int | None,
    requested_by: discord.abc.User | None = None,
):
    """Write one JSON diagnostic log for a queue lookup: /queue_stats, an automatic result post, a startup
    catch-up, a mention/`score?` reply in a queue channel, or a `?c <number>` reply in any channel
    (`trigger`, one of QUEUE_LOG_TRIGGERS). Never includes tokens or raw API responses."""
    os.makedirs(QUEUE_STATS_LOG_DIR, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y%m%dT%H%M%S.%fZ")
    safe_match_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(match_id))
    user_id = requested_by.id if requested_by is not None else None
    source = f"u{user_id}" if user_id is not None else trigger
    # A random tag keeps two lookups of the same queue in the same clock tick from sharing a name.
    stem = f"queue_stats_{timestamp}_g{guild_id}_{source}_m{safe_match_id}"

    payload = {
        "event": trigger,
        "requested_at_utc": now_utc.isoformat(),
        "match_id": str(match_id),
        "guild_id": guild_id,
        "channel_id": channel_id,
        "requested_by": {
            "discord_id": user_id,
            "username": str(requested_by),
            "display_name": getattr(requested_by, "display_name", None),
        } if requested_by is not None else None,
        "status": "error" if error_text else "ok",
        "error": error_text,
        "result_summary": None,
        "individual_games": [],
        "diagnostics": match_result.get("diagnostics") if match_result else None,
    }

    if match_result and "teams" in match_result:
        teams = match_result.get("teams") or []
        payload["result_summary"] = {
            "winning_team_index": match_result.get("winning_team_index"),
            "team_round_wins": match_result.get("team_round_wins"),
            "match_history": match_result.get("match_history"),
            "total_games_played": match_result.get("total_games_played"),
            "team_count": len(teams),
            "players_per_team": [len(team) for team in teams],
            "is_final": match_result.get("is_final"),
            "match_start_ms": match_result.get("match_start_ms"),
            "match_end_ms": match_result.get("match_end_ms"),
            "duration_ms": match_result.get("duration_ms"),
        }
        payload["individual_games"] = match_result.get("individual_games") or []

    # Written to a .tmp name (created exclusively), then published under its final name with a hard link, which
    # fails rather than replace an existing file, so a reader (the operator panel) never sees half a file and a
    # name collision never overwrites another lookup's log: it retries with a fresh tag.
    for _attempt in range(5):
        tag = secrets.token_hex(4)
        tmp_path = os.path.join(QUEUE_STATS_LOG_DIR, f"{stem}_r{tag}.json.tmp")
        file_path = os.path.join(QUEUE_STATS_LOG_DIR, f"{stem}_r{tag}.json")
        try:
            log_file = open(tmp_path, "x", encoding="utf-8")
        except FileExistsError:
            continue
        try:
            with log_file:
                json.dump(payload, log_file, ensure_ascii=True, indent=2)
            try:
                os.link(tmp_path, file_path)
            except FileExistsError:
                raise
            except OSError:  # a filesystem without hard links: rename, unless the name is already taken
                if os.path.lexists(file_path):
                    raise FileExistsError(file_path) from None
                os.replace(tmp_path, file_path)
        except FileExistsError:
            continue
        finally:
            try:
                os.remove(tmp_path)  # only this call's own file: it created the .tmp exclusively
            except OSError:
                pass
        break
    else:
        raise FileExistsError(f"no free diagnostic log name for Queue#{match_id}")

    prune_queue_stats_logs(QUEUE_STATS_LOG_DIR)


async def save_queue_stats_log(match_id: str, match_result: dict | None, error_text: str | None, **context):
    """write_queue_stats_log off the event loop. A failed write is logged and swallowed, so it never stops a
    result from being posted."""
    try:
        await asyncio.to_thread(write_queue_stats_log, match_id, match_result, error_text, **context)
    except (OSError, TypeError, ValueError) as exc:
        log.warning("Could not write the diagnostic log for Queue#%s: %r", match_id, exc)


def check_queue_hall_of_fame_records(
    teams: list[list[dict]],
    match_id: str,
    guild_id: int,
    total_games_played: int,
    duration_ms: int | None = None,
    min_games_required: int = MIN_GAMES_FOR_HALL_OF_FAME_UPDATE,
    snapshot_games: list[dict] | None = None,
) -> list[str]:
    """Scans a queue's final teams for new all-time bests, updates the DB, and returns announcement lines.
    `snapshot_games` is the queue snapshot's per-game rows (build_queue_snapshot), for the single-game damage record."""
    if total_games_played < min_games_required:
        return []

    best_kills: tuple[int, dict] | None = None
    best_avg_damage: tuple[float, dict] | None = None
    for team in teams:
        for entry in team:
            stats = entry["stats"]
            games = stats.get("games", 0)
            # A sub who only played a fraction of the queue shouldn't be able to claim an
            # all-time record off one lucky round, even though the queue overall qualifies.
            if games < min_games_required:
                continue

            kills = stats.get("kills", 0)
            if best_kills is None or kills > best_kills[0]:
                best_kills = (kills, entry)

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

    # Any player's single game counts here, a sub's too: one game is the whole stat, so the per-player floor above
    # doesn't apply. The queue itself still needs min_games_required games.
    best_game = best_game_damage(snapshot_games or [])
    if best_game is not None and best_game[0] > 0:
        damage, row, game_number = best_game
        holder_name = row.get("name") or "Unknown"
        if try_set_hall_of_fame_record(
            "most_damage_game", float(damage), row.get("discord_id"), holder_name, match_id, guild_id,
            game_number=game_number,
        ):
            announcements.append(
                f"🏆 New Hall of Fame record! **{HALL_OF_FAME_RECORDS['most_damage_game']}**: "
                f"{damage:,} by {holder_name} (Game {game_number})"
            )

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


def _snapshot_row(team_index: int, row: dict, entry: dict) -> dict:
    """One player's row in one game of a v2 snapshot. Linkage comes from this game row alone: a row is linked
    only when it has its own survev.de slug belonging to a linked user, so a logged-out `Raven` never takes the
    name or link of a logged-in `raven` the series aggregation merged it with (their identity, slug-else-name,
    is the same string). The link is the calculator's own lookup carried on the row (`discord_id`), so building a
    snapshot never touches the database. `key` keeps players apart across games: the slug, else the in-game name
    before guest numbering plus its number. It, `slug` and `username` are internal."""
    slug = row.get("slug") or None
    username = (row.get("username") or "").strip()
    discord_id = None
    if slug:
        if "discord_id" in row:
            discord_id = row.get("discord_id")
        elif entry.get("slug") == slug:
            discord_id = entry.get("discord_id")
    same_identity = (
        entry.get("discord_id") == discord_id
        and (entry.get("slug") or None) == slug
        and (entry.get("username") or "").strip().lower() == username.lower()
    )
    if same_identity:
        # This side's own entry for this exact game identity (linked, or a guest entry): the card's name.
        name = entry.get("display_name") or entry.get("username") or username or "Unknown"
    else:
        # The aggregation merged this row into another identity's entry (slug-or-lowercased-name collision, in
        # either arrival order): the row keeps its own game name, guest numbering included.
        name = username or "Unknown"
    return {
        "team": team_index,
        "key": f"slug:{slug}" if slug else
        f"name:{(row.get('base_username') or username).strip().lower()}#{row.get('guest_number') or 1}",
        "name": name,
        "username": username,
        "slug": slug,
        "discord_id": discord_id,
        "guest": discord_id is None,
        "kills": row.get("kills") or 0,
        "damage": row.get("damage_dealt") or 0,
        "impact": row.get("impact_score"),
    }


def build_queue_snapshot(match_id: str, match_result: dict) -> dict:
    """The compact, JSON-safe slice of a finished queue that the Game breakdown needs: for every game, who played
    on which displayed team and their kills, damage and impact. Lobby strangers outside the two shown teams are
    left out, and names are the display names already resolved for the result image."""
    # Keyed by team too: someone who switched sides mid-series has one entry per side.
    entries = {
        (team_index, player_identity(entry.get("slug"), entry.get("username") or "")): entry
        for team_index, team in enumerate(match_result.get("teams") or []) for entry in team
    }
    games = []
    for game in match_result.get("individual_games") or []:
        rows = []
        for row in game.get("players") or []:
            team_index = row.get("display_team_index")
            entry = entries.get((team_index, row.get("identity")))
            if team_index is None or entry is None:
                continue
            rows.append(_snapshot_row(team_index, row, entry))
        games.append({"number": game.get("game_number"), "winner": game.get("winning_display_team_index"), "players": rows})

    return {
        "v": QUEUE_SNAPSHOT_VERSION,
        "match_id": str(match_id),
        "winning_team_index": match_result.get("winning_team_index"),
        "team_round_wins": match_result.get("team_round_wins"),
        "match_history": match_result.get("match_history"),
        "games": games,
    }


async def build_queue_stats_payload(match_id: str, guild_id: int):
    """Runs the NeatQueue/survev.de cross-reference and returns either
    (content, discord.File, None, match_result) on success. On failure the fourth value
    carries diagnostics only, so the command can still log why the lookup failed."""
    diagnostics = {}
    match_result, error = await calculate_queue_match_stats(match_id, guild_id, diagnostics=diagnostics)
    if error:
        return None, None, f"❌ {error}", {"diagnostics": diagnostics}

    teams = match_result["teams"]
    if not any(teams):
        return None, None, "This NeatQueue match has no player roster to show stats for.", {"diagnostics": diagnostics}

    await resolve_queue_user_display_names(teams, bot)
    image_buffer = await asyncio.to_thread(
        generate_queue_result_image,
        match_id,
        teams,
        match_result["winning_team_index"],
        match_result.get("match_history"),
        match_result.get("team_round_wins"),
        duration_ms=match_result.get("duration_ms"),
        is_final=bool(match_result.get("is_final")),
        game_winners=[game.get("winning_display_team_index") for game in match_result.get("individual_games") or []]
        or None,
    )
    file = discord.File(image_buffer, filename=queue_result_filename(match_id))

    content = f"Queue stats for NeatQueue#{match_id}" if match_result.get("is_final") else f"NeatQueue#{match_id} - IN PROGRESS"
    total_games_played = int(match_result.get("total_games_played") or len(match_result.get("match_history") or []))

    snapshot = None
    if match_result.get("is_final"):
        try:
            snapshot = build_queue_snapshot(match_id, match_result)
        except (TypeError, ValueError) as exc:
            log.warning("Could not build the game breakdown for Queue#%s: %r", match_id, exc)

    # The writes run in a worker thread, one result at a time, so a write waiting on a database lock (the website
    # reads inside short transactions) never stalls the event loop: Discord, interactions and SIGTERM carry on.
    record_announcements = await asyncio.to_thread(
        _store_queue_result, teams, match_id, guild_id, total_games_played, match_result, snapshot)
    if record_announcements:
        content += "\n" + "\n".join(record_announcements)
    return content, file, None, match_result


# One writer at a time for a queue's results and for the Hall of Fame reset. The worker thread itself holds it, so
# a caller that's cancelled mid-write can't let the next result (or a reset) in while this one's writes still run.
_RESULT_WRITE_LOCK = threading.Lock()


def _store_queue_result(*args) -> list[str]:
    with _RESULT_WRITE_LOCK:
        return _store_queue_result_locked(*args)


def reset_hall_of_fame_records() -> int:
    """Delete every Hall of Fame record, never between one queue's record updates. Blocking: run it in a thread."""
    with _RESULT_WRITE_LOCK:
        return clear_hall_of_fame_records()


def _store_queue_result_locked(teams, match_id, guild_id, total_games_played, match_result, snapshot) -> list[str]:
    """Hall of Fame records, then the stat rows, then the game breakdown, in that order (blocking SQLite writes).
    Returns the record announcements."""
    # Only a finished queue can set records: an unfinished one has partial stats, and its duration runs up to "now".
    record_announcements = []
    if match_result.get("is_final"):
        record_announcements = check_queue_hall_of_fame_records(
            teams,
            match_id,
            guild_id,
            total_games_played,
            duration_ms=match_result.get("duration_ms"),
            snapshot_games=(snapshot or {}).get("games"),
        )

    cache_player_queue_stats(match_id, guild_id, match_result)

    if snapshot is not None:
        try:
            save_queue_result(guild_id, match_id, snapshot)
        except (sqlite3.Error, TypeError, ValueError) as exc:
            # The result must still be posted; the Game breakdown button just reports it as unavailable.
            log.warning("Could not save the game breakdown for Queue#%s: %r", match_id, exc)
    return record_announcements


def queue_games_unavailable(match_id: str | None) -> str:
    rebuild = f"`/queue_stats {match_id}`" if match_id else "`/queue_stats` with the queue number"
    if QUEUE_RESULT_RETENTION_DAYS > 0:
        reason = f"it was posted before breakdowns were saved, or it's older than {QUEUE_RESULT_RETENTION_DAYS} days"
    else:
        reason = "it was posted before breakdowns were saved"
    return f"The game breakdown isn't available for this queue: {reason}. Run {rebuild} to rebuild it."


def _top_performer(rows: list[dict], key: str) -> tuple[str, int] | None:
    """(name, value) of the player with the highest `key` in this game, ignoring players without a value."""
    scored = [row for row in rows if row.get(key) is not None]
    if not scored:
        return None
    best = max(scored, key=lambda row: row[key])
    return best["name"], int(best[key] + 0.5)


def load_queue_snapshot(guild_id: int | None, match_id: str) -> dict | None:
    """The saved Game breakdown snapshot for a queue, or None. Read once per click, so the card and its menu agree."""
    return get_queue_result(guild_id, match_id) if guild_id is not None else None


def queue_game_choices(snapshot: dict | None) -> list[tuple[str, str]]:
    """(label, description) per game for the Game breakdown's "Jump to game" menu: who won it and who had the most
    average damage (in one game, a player's average damage is that game's damage). Empty when nothing is saved.
    The view shows at most 25 of them (Discord's limit), around the current game."""
    choices = []
    for index, game in enumerate((snapshot or {}).get("games") or []):
        number = game.get("number") or index + 1
        winner = game.get("winner")
        label = f"Game {number} · Team {winner + 1} won" if isinstance(winner, int) and winner >= 0 else \
            f"Game {number} · winner unknown"
        top = _top_performer(game.get("players") or [], "damage")
        description = f"Top avg dmg: {top[0]} ({top[1]:,})" if top else "No damage recorded"
        choices.append((label[:100], description[:100]))
    return choices


def _game_alt_text(match_id: str, number: int, total: int, headline: str, teams: list[list[dict]]) -> str:
    """Text alternative for the game card: the headline plus every player's numbers (Discord caps alt text at 1024)."""
    lines = [f"Game {number} of {total} of queue {match_id}. {headline}."]
    for index, team in enumerate(teams, start=1):
        parts = []
        for entry in team:
            stats = entry["stats"]
            part = f"{entry['display_name']} {stats['kills']} kills, {stats['damage']:,} damage"
            if stats["impact"] is not None:
                part += f", impact {int(stats['impact'] + 0.5)}"
            parts.append(part)
        lines.append(f"Team {index}: {'; '.join(parts) if parts else 'no players'}.")
    return " ".join(lines)[:1000]


async def build_queue_game_payload(guild_id: int | None, match_id: str, page: int, snapshot: dict | None = None):
    """One game of a finished queue's Game breakdown, from its saved snapshot (read here unless the caller already
    has it). Returns (embed, file, page, total_games, None), or (None, None, 0, 0, error_text) when nothing is saved."""
    if snapshot is None:
        snapshot = load_queue_snapshot(guild_id, match_id)
    games = (snapshot or {}).get("games") or []
    if not games:
        return None, None, 0, 0, queue_games_unavailable(match_id)

    total = len(games)
    page = max(0, min(page, total - 1))
    game = games[page]
    number = game.get("number") or page + 1
    rows = game.get("players") or []
    team_count = max(2, len(snapshot.get("team_round_wins") or []), 1 + max((row["team"] for g in games for row in g["players"]), default=0))
    teams: list[list[dict]] = [[] for _ in range(team_count)]
    for row in rows:
        teams[row["team"]].append({
            "display_name": row["name"],
            "username": row["name"],
            "guest": row.get("guest", False),
            "stats": {"games": 1, "wins": 0, "kills": row["kills"], "damage": row["damage"], "impact": row.get("impact")},
        })
    for team in teams:
        team.sort(key=lambda entry: entry["stats"]["damage"], reverse=True)

    winner = game.get("winner")
    series_score = [sum(1 for played in games[:page + 1] if played.get("winner") == index) for index in range(team_count)]
    image_buffer = await asyncio.to_thread(
        generate_queue_game_image, match_id, number, total, teams, winner, snapshot.get("match_history"), series_score,
        [played.get("winner") for played in games],
    )

    if winner is not None and 0 <= winner < team_count:
        headline = f"Team {winner + 1} won Game {number}"
    else:
        headline = f"Winner of Game {number} unknown"
    summary = [f"**{headline}** · Series {' - '.join(str(score) for score in series_score)}"]
    top_damage, top_impact = _top_performer(rows, "damage"), _top_performer(rows, "impact")
    if top_damage:
        summary.append(f"Top damage: **{top_damage[0]}** ({top_damage[1]:,})")
    if top_impact:
        summary.append(f"Top impact: **{top_impact[0]}** ({top_impact[1]})")

    filename = f"queue_{match_id}_game_{number}.png"
    embed = discord.Embed(
        title=f"Game {number} of {total} · Queue #{match_id}",
        description="\n".join(summary),
        color=discord.Color.blurple(),
    )
    embed.set_image(url=f"attachment://{filename}")
    if has_impact_scores(teams):
        embed.set_footer(text="Impact is survev.de's per-game impact score (0-100).")
    file = discord.File(
        image_buffer, filename=filename,
        description=_game_alt_text(match_id, number, total, headline, teams),
    )
    return embed, file, page, total, None


async def build_hall_of_fame_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Hall of Fame",
        description="The best single-queue stats the bot has recorded, across every server.",
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
        elif record_type == "most_damage_game":
            holder = f"<@{record['discord_id']}>" if record["discord_id"] else record["display_name"]
            where = f"Queue #{record['match_id']}"
            if record.get("game_number"):
                where += f", Game {record['game_number']}"
            field_value = f"**{record['value']:,.0f}** — {holder} ({where})"
        else:
            value_text = str(int(record["value"]))
            holder = f"<@{record['discord_id']}>" if record["discord_id"] else record["display_name"]
            field_value = f"**{value_text}** — {holder} (Queue #{record['match_id']})"

        embed.add_field(name=label, value=field_value, inline=False)
    return embed
