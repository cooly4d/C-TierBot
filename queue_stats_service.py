"""Ties NeatQueue match calculation to hall-of-fame record tracking, the /queue_stats
result image, and per-command JSON logging.
"""
import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import discord

from bot_config import HALL_OF_FAME_RECORDS, MIN_GAMES_FOR_HALL_OF_FAME_UPDATE, QUEUE_STATS_LOG_DIR, bot
from db import (
    QUEUE_RESULT_RETENTION_DAYS,
    cache_player_queue_stats,
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

QUEUE_SNAPSHOT_VERSION = 1
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


def write_queue_stats_command_log(
    interaction: discord.Interaction,
    match_id: str,
    match_result: dict | None,
    error_text: str | None,
):
    """Write one JSON log entry per /queue_stats command execution."""
    os.makedirs(QUEUE_STATS_LOG_DIR, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y%m%dT%H%M%S.%fZ")
    safe_match_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(match_id))
    guild_id = interaction.guild_id
    user_id = interaction.user.id if interaction.user else None
    file_name = f"queue_stats_{timestamp}_g{guild_id}_u{user_id}_m{safe_match_id}.json"
    file_path = os.path.join(QUEUE_STATS_LOG_DIR, file_name)

    payload = {
        "event": "queue_stats_command",
        "requested_at_utc": now_utc.isoformat(),
        "match_id": str(match_id),
        "guild_id": guild_id,
        "channel_id": interaction.channel_id,
        "requested_by": {
            "discord_id": user_id,
            "username": str(interaction.user) if interaction.user else None,
            "display_name": getattr(interaction.user, "display_name", None),
        },
        "status": "error" if error_text else "ok",
        "error": error_text,
        "result_summary": None,
        "individual_games": [],
    }

    if match_result:
        teams = match_result.get("teams") or []
        payload["result_summary"] = {
            "winning_team_index": match_result.get("winning_team_index"),
            "team_round_wins": match_result.get("team_round_wins"),
            "match_history": match_result.get("match_history"),
            "total_games_played": match_result.get("total_games_played"),
            "team_count": len(teams),
            "players_per_team": [len(team) for team in teams],
        }
        payload["individual_games"] = match_result.get("individual_games") or []

    with open(file_path, "w", encoding="utf-8") as log_file:
        json.dump(payload, log_file, ensure_ascii=True, indent=2)


def check_queue_hall_of_fame_records(
    teams: list[list[dict]],
    match_id: str,
    guild_id: int,
    total_games_played: int,
    duration_ms: int | None = None,
    min_games_required: int = MIN_GAMES_FOR_HALL_OF_FAME_UPDATE,
) -> list[str]:
    """Scans a queue's final teams for new all-time bests, updates the DB, and returns announcement lines."""
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


def build_queue_snapshot(match_id: str, match_result: dict) -> dict:
    """The compact, JSON-safe slice of a finished queue that the Game breakdown needs: for every game, who played
    on which displayed team and their kills, damage and impact. Lobby strangers outside the two shown teams are
    left out, and names are the display names already resolved for the result image."""
    entries = {
        player_identity(entry.get("slug"), entry.get("username") or ""): entry
        for team in match_result.get("teams") or [] for entry in team
    }
    games = []
    for game in match_result.get("individual_games") or []:
        rows = []
        for row in game.get("players") or []:
            team_index = row.get("display_team_index")
            entry = entries.get(row.get("identity"))
            if team_index is None or entry is None:
                continue
            rows.append({
                "team": team_index,
                "name": entry.get("display_name") or entry.get("username") or "Unknown",
                "discord_id": entry.get("discord_id"),
                "guest": bool(entry.get("guest")),
                "kills": row.get("kills") or 0,
                "damage": row.get("damage_dealt") or 0,
                "impact": row.get("impact_score"),
            })
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
    (content, discord.File, None, match_result) on success or (None, None, error_text, None) on failure."""
    match_result, error = await calculate_queue_match_stats(match_id, guild_id)
    if error:
        return None, None, f"❌ {error}", None

    teams = match_result["teams"]
    if not any(teams):
        return None, None, "This NeatQueue match has no player roster to show stats for.", None

    await resolve_queue_user_display_names(teams, bot)
    image_buffer = await asyncio.to_thread(
        generate_queue_result_image,
        match_id,
        teams,
        match_result["winning_team_index"],
        match_result.get("match_history"),
        match_result.get("team_round_wins"),
        duration_ms=match_result.get("duration_ms"),
    )
    file = discord.File(image_buffer, filename=queue_result_filename(match_id))

    content = f"Queue stats for NeatQueue#{match_id}"
    total_games_played = int(match_result.get("total_games_played") or len(match_result.get("match_history") or []))

    # Only a finished queue can set records: an unfinished one has partial stats, and its duration runs up to "now".
    record_announcements = []
    if match_result.get("is_final"):
        record_announcements = check_queue_hall_of_fame_records(
            teams,
            match_id,
            guild_id,
            total_games_played,
            duration_ms=match_result.get("duration_ms"),
        )
    if record_announcements:
        content += "\n" + "\n".join(record_announcements)

    cache_player_queue_stats(match_id, guild_id, match_result)

    if match_result.get("is_final"):
        try:
            save_queue_result(guild_id, match_id, build_queue_snapshot(match_id, match_result))
        except (sqlite3.Error, TypeError, ValueError) as exc:
            # The result must still be posted; the Game breakdown button just reports it as unavailable.
            print(f"[Queue] Could not save the game breakdown for Queue#{match_id}: {exc!r}")
    return content, file, None, match_result


def queue_games_unavailable(match_id: str | None) -> str:
    rebuild = f"`/queue_stats {match_id}`" if match_id else "`/queue_stats` with the queue number"
    return (
        "The game breakdown isn't available for this queue: it was posted before breakdowns were saved, "
        f"or it's older than {QUEUE_RESULT_RETENTION_DAYS} days. Run {rebuild} to rebuild it."
    )


def _top_performer(rows: list[dict], key: str) -> tuple[str, int] | None:
    """(name, value) of the player with the highest `key` in this game, ignoring players without a value."""
    scored = [row for row in rows if row.get(key) is not None]
    if not scored:
        return None
    best = max(scored, key=lambda row: row[key])
    return best["name"], int(best[key] + 0.5)


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


async def build_queue_game_payload(guild_id: int | None, match_id: str, page: int):
    """One game of a finished queue's Game breakdown, from its saved snapshot.
    Returns (embed, file, page, total_games, None), or (None, None, 0, 0, error_text) when nothing is saved."""
    snapshot = get_queue_result(guild_id, match_id) if guild_id is not None else None
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
        title="🏛️ NeatQueue Hall of Fame",
        description="All-time best single-queue stats recorded by /queue_stats.",
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
        else:
            value_text = str(int(record["value"]))
            holder = f"<@{record['discord_id']}>" if record["discord_id"] else record["display_name"]
            field_value = f"**{value_text}** — {holder} (Queue #{record['match_id']})"

        embed.add_field(name=label, value=field_value, inline=False)
    embed.set_footer(text="Data courtesy of NeatQueue & survev.de APIs :)")
    return embed
