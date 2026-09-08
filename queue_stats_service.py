"""Ties NeatQueue match calculation to hall-of-fame record tracking, the /queue_stats
result image, and per-command JSON logging.
"""
import json
import os
import re
from datetime import datetime, timezone

import discord

from bot_config import HALL_OF_FAME_RECORDS, MIN_GAMES_FOR_HALL_OF_FAME_UPDATE, QUEUE_STATS_LOG_DIR, bot
from db import get_hall_of_fame_record, try_set_hall_of_fame_record
from image_utils import generate_queue_result_image
from neatqueue_client import calculate_queue_match_stats, resolve_queue_user_display_names


def format_duration_ms(duration_ms: int) -> str:
    total_seconds = max(0, int(duration_ms)) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


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
            kills = stats.get("kills", 0)
            if best_kills is None or kills > best_kills[0]:
                best_kills = (kills, entry)

            games = stats.get("games", 0)
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
    image_buffer = generate_queue_result_image(
        match_id,
        teams,
        match_result["winning_team_index"],
        match_result.get("match_history"),
        match_result.get("team_round_wins"),
    )
    file = discord.File(image_buffer, filename=f"queue_stats_{match_id}.png")

    content = f"Queue stats for NeatQueue#{match_id}"
    total_games_played = int(match_result.get("total_games_played") or len(match_result.get("match_history") or []))

    record_announcements = check_queue_hall_of_fame_records(
        teams,
        match_id,
        guild_id,
        total_games_played,
        duration_ms=match_result.get("duration_ms"),
    )
    if record_announcements:
        content += "\n" + "\n".join(record_announcements)
    return content, file, None, match_result


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
