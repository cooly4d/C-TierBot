"""NeatQueue API access, response parsing, and the core cross-referencing logic that
turns a NeatQueue match number + survev.de match data into one finalized result.
"""
import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone

import aiohttp
import discord

from bot_config import (
    API_CONCURRENCY_LIMIT,
    NEATQUEUE_API_BASE,
    NEATQUEUE_API_TOKEN,
    NEATQUEUE_BOT_ID,
    QUEUE_MATCH_FALLBACK_DURATION_MS,
    bot,
)
from db import get_guild_queue_channels, get_discord_id_by_slug, get_user_token
from survev_client import fetch_player_matches_in_window, fetch_public_match_data, get_match_history_timestamp


async def fetch_neatqueue_history(session: aiohttp.ClientSession, guild_id: int, match_number: str | None = None, extra_params: dict | None = None):
    """Fetches history for the given Discord guild's NeatQueue server (NeatQueue's server_id IS the
    guild id), optionally limited to one game number or filtered by extra_params."""
    if not guild_id:
        return None, "No Discord server ID given to look up NeatQueue history for."

    url = f"{NEATQUEUE_API_BASE}/history/{guild_id}"
    params = None
    if match_number is not None:
        params = {
            "start_game_number": str(match_number),
            "end_game_number": str(match_number)
        }
    if extra_params:
        params = {**(params or {}), **extra_params}

    headers = {
        "Authorization": f"Bearer {NEATQUEUE_API_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        async with session.get(url, headers=headers, params=params) as resp:
            text = await resp.text()
            status = resp.status
    except aiohttp.ClientError as exc:
        return None, f"NeatQueue API request failed for {url}: {exc}"

    if status != 200:
        error_detail = text
        try:
            error_detail_json = json.loads(text)
            error_detail = error_detail_json
        except Exception:
            pass
        if status == 404:
            return None, (
                f"NeatQueue endpoint returned 404 for URL {url}. "
                f"Verify this server has NeatQueue set up and the match number is correct; response detail: {error_detail}"
            )
        return None, f"NeatQueue API returned {status} for URL {url}: {error_detail}"

    try:
        data = json.loads(text)
    except Exception as exc:
        return None, f"NeatQueue returned invalid JSON from {url}: {exc}"

    return data, None


async def fetch_neatqueue_matches_since(session: aiohttp.ClientSession, guild_id: int, start_date_iso: str):
    """Fetches every match for the given guild's NeatQueue server that finished at/after start_date, oldest first."""
    data, error = await fetch_neatqueue_history(session, guild_id, extra_params={
        "start_date": start_date_iso,
        "order": "asc",
        "page_size": "1000"
    })
    if error:
        return None, error
    return extract_neatqueue_entries(data), None


def extract_neatqueue_entries(history_data):
    if isinstance(history_data, list):
        return history_data
    if isinstance(history_data, dict):
        for key in ("matches", "history", "data", "entries"):
            if isinstance(history_data.get(key), list):
                return history_data[key]
        return [history_data]
    return []


def get_nested_value(entry, key):
    if not isinstance(entry, dict):
        return None
    if key in entry:
        return entry[key]
    for value in entry.values():
        if isinstance(value, dict):
            nested = get_nested_value(value, key)
            if nested is not None:
                return nested
    return None


def parse_neatqueue_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            try:
                dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except Exception:
                return None
    return None


def get_match_start_ms(match):
    for key in ("start_time_ms", "start_time", "time", "timestamp"):
        value = get_nested_value(match, key)
        parsed = parse_neatqueue_time(value)
        if parsed is not None:
            return parsed
    return None


async def get_match_end_ms(match, default_start_ms: int, diagnostics: dict | None = None):
    def record_end(value, source):
        if diagnostics is not None:
            diagnostics["end_source"] = source
        return value

    # NeatQueue edits the winner message in place once the whole series (all games) has finished, so that
    # message's actual edited_at (fetched live) is the most reliable end boundary we can get — far better
    # than guessing a fixed duration. Every candidate below is clamped to be after start_ms, since a stale
    # or replayed history entry could otherwise hand us a "winner_message" that predates the match.
    winner_message_id = get_nested_value(match, "winner_message")
    winner_channel_id = get_nested_value(match, "winner_channel")
    if winner_message_id and winner_channel_id:
        try:
            channel = bot.get_channel(int(winner_channel_id)) or await bot.fetch_channel(int(winner_channel_id))
            winner_message = await channel.fetch_message(int(winner_message_id))
            end_dt = winner_message.edited_at or winner_message.created_at
            end_ms = int(end_dt.timestamp() * 1000) + 5000
            if end_ms > default_start_ms:
                return record_end(end_ms, "winner_message_edited_at" if winner_message.edited_at else "winner_message_created_at")
        except (discord.HTTPException, discord.Forbidden, discord.NotFound, TypeError, ValueError, AttributeError) as exc:
            if diagnostics is not None:
                diagnostics["winner_message_lookup_error"] = type(exc).__name__

    if winner_message_id:
        try:
            end_dt = discord.utils.snowflake_time(int(winner_message_id))
            end_ms = int(end_dt.timestamp() * 1000) + 5000
            if end_ms > default_start_ms:
                return record_end(end_ms, "winner_message_snowflake")
        except (TypeError, ValueError):
            pass

    end_ms = get_nested_value(match, "end_time_ms")
    parsed_end = parse_neatqueue_time(end_ms)
    if parsed_end is not None and parsed_end > default_start_ms:
        return record_end(parsed_end, "neatqueue_end_time_ms")

    player_timestamps = []
    for team in collect_neatqueue_teams(match):
        for player in team:
            timestamp_value = get_nested_value(player, "timestamp") or get_nested_value(player, "time")
            parsed = parse_neatqueue_time(timestamp_value)
            if parsed is not None:
                player_timestamps.append(parsed)

    if player_timestamps:
        latest = max(player_timestamps) + 5000
        if latest > default_start_ms:
            return record_end(latest, "latest_player_timestamp")

    return record_end(default_start_ms + QUEUE_MATCH_FALLBACK_DURATION_MS, "fallback_duration")


NEATQUEUE_MATCH_ID_KEYS = ("match_id", "id", "matchId", "game_id", "gameId", "server_match_id", "game_number", "gameNumber")


def derive_team_round_wins_from_round_winners(round_winner_display_indices: list[int | None], num_teams: int) -> list[int]:
    """Count how many rounds each displayed team actually won from the per-round winner indices."""
    team_round_wins = [0] * max(num_teams, 0)
    for winner_idx in round_winner_display_indices:
        if winner_idx is None:
            continue
        if 0 <= winner_idx < len(team_round_wins):
            team_round_wins[winner_idx] += 1
    return team_round_wins


def get_match_game_number(match):
    for key in NEATQUEUE_MATCH_ID_KEYS:
        value = get_nested_value(match, key)
        if value is not None:
            return value
    return None


def _collect_neatqueue_player_dicts(data):
    """Recursively flattens an arbitrarily nested players/teams structure into a flat list of player dicts."""
    if isinstance(data, dict):
        if "id" in data and isinstance(data["id"], str):
            return [data]
        players = []
        for value in data.values():
            players.extend(_collect_neatqueue_player_dicts(value))
        return players
    if isinstance(data, list):
        players = []
        for item in data:
            players.extend(_collect_neatqueue_player_dicts(item))
        return players
    return []


def collect_neatqueue_teams(match):
    """Returns a list of teams (one list of player dicts per team), preserving NeatQueue's team_num order."""
    if not isinstance(match, dict):
        return []

    # NeatQueue's own schema: "teams" is a list of teams, each a list of player dicts.
    teams_field = match.get("teams")
    if isinstance(teams_field, list) and teams_field:
        return [_collect_neatqueue_player_dicts(team) for team in teams_field]

    players_field = match.get("players")
    if isinstance(players_field, list) and players_field:
        # Already grouped by team (list of lists)?
        if isinstance(players_field[0], list):
            return [_collect_neatqueue_player_dicts(team) for team in players_field]
        return [_collect_neatqueue_player_dicts(players_field)]

    return []


async def find_queue_panel_message(guild_id: int, match_id: str):
    """Searches the guild's configured results channels for the (possibly still in-progress) NeatQueue
    panel/winner message for this match number — used when NeatQueue's history API has no entry yet
    because the queue hasn't finished."""
    pattern = re.compile(rf"Queue#{re.escape(str(match_id))}\b")
    for channel_id in get_guild_queue_channels(guild_id):
        channel = bot.get_channel(channel_id)
        if channel is None:
            continue
        try:
            async for message in channel.history(limit=200):
                if message.author.id != NEATQUEUE_BOT_ID:
                    continue
                for embed in message.embeds:
                    if embed.title and pattern.search(embed.title):
                        return message
        except discord.HTTPException:
            continue
    return None


def parse_teams_from_panel_embed(embed: discord.Embed) -> list[list[dict]]:
    """Extracts team rosters (as Discord IDs) from a NeatQueue panel/winner embed's "Team N" fields,
    which list each player as a real @mention even before the queue has a result."""
    teams = []
    for field in embed.fields:
        if not field.name or not field.name.lower().startswith("team"):
            continue
        ids = re.findall(r"<@!?(\d+)>", field.value or "")
        teams.append([{"id": pid} for pid in ids])
    return teams


async def get_user_display_name(client: discord.Client, discord_id: int) -> str:
    member = next((g.get_member(discord_id) for g in getattr(client, "guilds", ()) if g.get_member(discord_id) is not None), None)
    if member is None:
        member = next((m for m in client.get_all_members() if m.id == discord_id), None)
    if member is not None:
        return member.display_name

    user = client.get_user(discord_id)
    if user is None:
        try:
            user = await client.fetch_user(discord_id)
        except Exception:
            user = None

    if user is not None:
        return user.name
    return f"Player {discord_id}"


async def resolve_queue_user_display_names(teams: list[list[dict]], client: discord.Client):
    for team in teams:
        for entry in team:
            discord_id = entry.get("discord_id")
            if discord_id is None:
                # No /verify'd Discord user owns this slug — show their real survev.de name instead.
                entry["display_name"] = entry.get("username") or "Unknown"
                continue
            entry["display_name"] = await get_user_display_name(client, discord_id)


# survev.de saves spectators with this placeholder rank (the game server's getPlayersSortedByRank). A spectator can
# sit in a team_id that a queue team used in an earlier game, so they must never be counted as a player.
SURVEV_SPECTATOR_RANK = 999
QUEUE_TEAM_COUNT = 2
# Smallest team a real queue game can have (a 3v4 after someone leaves still counts); anything smaller is a side game.
MIN_QUEUE_TEAM_SIZE = 2


def parse_rank(raw) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_round_sides(board: list[dict], roster_side) -> tuple[dict | None, str, dict]:
    """Which survev.de team_id was NeatQueue's Team 1 and Team 2 in one game, as ({team_id: side}, "accepted", details),
    or (None, reason, details) when the game isn't one of this queue's. survev.de renumbers teams every game, so this is
    decided game by game from where the roster's linked players (`roster_side(slug)` -> side or None) actually played."""
    members: dict = {}
    for row in board:
        if parse_rank(row.get("rank")) == SURVEV_SPECTATOR_RANK:
            continue
        if row.get("team_id") is None or not player_identity(row.get("slug"), (row.get("username") or "").strip()):
            continue
        members.setdefault(row["team_id"], []).append(row)

    votes = {
        team_id: Counter(side for side in (roster_side(row.get("slug")) for row in rows) if side is not None)
        for team_id, rows in members.items()
    }
    details = {
        "playing_teams": len(members),
        "team_sizes": {str(team_id): len(rows) for team_id, rows in members.items()},
        # Linked roster players per survev.de team, counted per NeatQueue team: {"2": [3, 0]} = three Team 1 players.
        "roster_votes": {
            str(team_id): [counter[side] for side in range(QUEUE_TEAM_COUNT)]
            for team_id, counter in votes.items() if counter
        },
    }

    sides = {}
    for team_id, counter in votes.items():
        ranked = counter.most_common()
        if not ranked:
            continue
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None, "roster_sides_conflict", details
        sides[team_id] = ranked[0][0]
    if not sides:
        return None, "no_roster_players", details
    if len(set(sides.values())) != len(sides):
        # e.g. a warm-up with shuffled teams: one NeatQueue team's players spread over several survev.de teams.
        return None, "roster_sides_conflict", details
    if len(sides) == 1:
        # Only one side has linked roster players here; the queue's lobbies hold exactly two teams, so the
        # one other playing team is the opponent. No other team means a false start, several mean another lobby.
        others = [team_id for team_id in members if team_id not in sides]
        if not others:
            return None, "one_playing_team", details
        if len(others) > 1:
            return None, "unknown_opponent", details
        sides[others[0]] = 1 - next(iter(sides.values()))
        details["inferred_team_id"] = others[0]
    if any(len(members[team_id]) < MIN_QUEUE_TEAM_SIZE for team_id in sides):
        return None, "team_too_small", details

    details["team_sides"] = {str(team_id): side for team_id, side in sides.items()}
    return sides, "accepted", details


def player_identity(slug: str | None, username: str) -> str:
    """Key that identifies one survev.de player across a series' games: their account slug, else their
    lowercased name (accounts without a public slug)."""
    return slug or username.lower()


def parse_impact_score(raw) -> int | float | None:
    """survev.de's per-game impact score (0-100), or None when the game wasn't rated for that player
    (they never really fought, or the map doesn't score impact)."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return raw


async def calculate_queue_match_stats(match_id: str, guild_id: int, diagnostics: dict | None = None):
    """Builds the queue's teams from survev.de's own match data (ground truth for who played each game and
    with whom), placing each game's two teams on NeatQueue's Team 1/Team 2 via the roster's linked players,
    then overlays a Discord display name wherever the player's slug matches a /verify'd user. Anyone without
    a linked account just keeps their survev.de username."""
    if diagnostics is None:
        diagnostics = {}
    diagnostics.update({
        "schema_version": 1,
        "lookup_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {},
        "anchors": [],
        "round_selection": [],
    })
    window = diagnostics["window"]
    async with aiohttp.ClientSession() as session:
        nq_data, error = await fetch_neatqueue_history(session, guild_id, match_id)
        entries = extract_neatqueue_entries(nq_data) if nq_data else []
        match = entries[0] if entries else None
        history_teams = collect_neatqueue_teams(match)

        # NeatQueue can return winner=-2 while a series is still running. Treat it as
        # pending so live lookups end at "now", rather than a finished-match fallback.
        winner = get_nested_value(match, "winner")
        is_finished = match is not None and winner not in (None, -2)
        diagnostics["neatqueue"] = {
            "history_lookup_failed": error is not None,
            "entry_count": len(entries),
            "selected_game_number": get_match_game_number(match),
            "winner": winner,
            "winner_message_id": get_nested_value(match, "winner_message"),
            "winner_channel_id": get_nested_value(match, "winner_channel"),
            "history_start_ms": get_match_start_ms(match),
            "history_end_ms": parse_neatqueue_time(get_nested_value(match, "end_time_ms")),
            "history_players_per_team": [len(team) for team in history_teams],
        }
        diagnostics["is_final"] = is_finished
        diagnostics["finality_reason"] = (
            "history_winner_is_not_null_or_pending" if is_finished else
            "history_winner_is_pending" if winner == -2 else
            "history_winner_is_null" if match is not None else "no_history_entry"
        )

        if is_finished:
            is_final = True
            # NeatQueue returns the requested match as the first entry in the result.

            start_ms = get_match_start_ms(match)
            if start_ms is None:
                return None, "NeatQueue match entry is missing a recognizable start time."

            window["start_source"] = "neatqueue_history"
            end_ms = await get_match_end_ms(match, start_ms, diagnostics=window)

            teams = history_teams
            diagnostics["roster_source"] = "neatqueue_history"
            if not teams:
                return None, "NeatQueue match entry contains no player roster."
        else:
            is_final = False
            # Keep using the live panel when available. A pending history entry can also
            # supply the roster and start if the panel is absent or unreadable.
            panel_message = await find_queue_panel_message(guild_id, match_id)
            diagnostics["panel"] = {"found": panel_message is not None}
            teams = None
            if panel_message is not None:
                diagnostics["panel"].update({
                    "message_id": getattr(panel_message, "id", None),
                    "channel_id": getattr(getattr(panel_message, "channel", None), "id", None),
                    "created_at_utc": panel_message.created_at.isoformat(),
                })
                for embed in panel_message.embeds:
                    parsed = parse_teams_from_panel_embed(embed)
                    if any(parsed):
                        teams = parsed
                        break
                diagnostics["panel"]["roster_readable"] = bool(teams)

            if teams:
                start_ms = int(panel_message.created_at.timestamp() * 1000)
                window["start_source"] = "panel_created_at"
                diagnostics["roster_source"] = "discord_panel"
            elif match is not None:
                start_ms = get_match_start_ms(match)
                if start_ms is None:
                    return None, "NeatQueue history entry is missing a recognizable start time, and no readable queue panel was found."
                if not any(history_teams):
                    return None, "NeatQueue history entry contains no player roster, and no readable queue panel was found."
                teams = history_teams
                window["start_source"] = "neatqueue_history"
                diagnostics["roster_source"] = "neatqueue_history"
            elif panel_message is not None:
                return None, "Couldn't read the player roster from NeatQueue's message for this queue."
            else:
                return None, error or "No NeatQueue history entry or readable queue panel was found for this queue."

            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            window["end_source"] = "lookup_current_time"

        window.update({
            "from_ms": start_ms,
            "to_ms": end_ms,
            "from_utc": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(),
            "to_utc": datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(),
        })

        # NeatQueue's team index per rostered discord_id: each game's survev.de teams are mapped onto these.
        neatqueue_team_index_by_discord_id: dict[int, int] = {}
        for i, team_players in enumerate(teams):
            for player in team_players:
                try:
                    neatqueue_team_index_by_discord_id[int(player.get("id"))] = i
                except (TypeError, ValueError):
                    continue

        # Step 1: at least one verified player is needed as an "anchor" — pull their own scoped match
        # history and use its guids to pin down exactly which survev.de match(es) this queue played.
        # This is far more reliable than any time-window guess (handles Best-of-N cleanly).
        sem = asyncio.Semaphore(API_CONCURRENCY_LIMIT)
        anchor_candidates: list[tuple[int, str]] = []
        for team_players in teams:
            for player in team_players:
                try:
                    discord_id = int(player.get("id"))
                except (TypeError, ValueError):
                    continue
                token = get_user_token(discord_id)
                if token:
                    anchor_candidates.append((discord_id, token))

        async def _fetch_anchor(discord_id: int, token: str):
            anchor_diagnostics = {"discord_id": discord_id}
            async with sem:
                own_matches = await fetch_player_matches_in_window(
                    session, token, start_ms, end_ms, diagnostics=anchor_diagnostics)
                anchor_diagnostics["matches"] = [
                    {"guid": m.get("guid"), "timestamp_ms": get_match_history_timestamp(m)}
                    for m in own_matches
                ]
                anchor_diagnostics["returned_count"] = len(own_matches)
                return discord_id, own_matches, anchor_diagnostics

        anchor_results = await asyncio.gather(*(_fetch_anchor(did, tok) for did, tok in anchor_candidates))
        anchor_guid_sets = []
        anchor_matches_by_discord: dict[int, list[dict]] = {}
        for discord_id, own_matches, anchor_diagnostics in anchor_results:
            diagnostics["anchors"].append(anchor_diagnostics)
            guids = {m.get("guid") for m in own_matches if m.get("guid")}
            if guids:
                anchor_guid_sets.append(guids)
                anchor_matches_by_discord[discord_id] = own_matches

        if not anchor_guid_sets:
            return None, "No linked (/verify'd) player was found in this queue — need at least one to find the match."

        # Every game any linked player played in the window is a candidate, and each one is then checked against the
        # roster (resolve_round_sides). Requiring every linked player to have played a game dropped real games
        # whenever a linked substitute joined late or someone logged in partway through the series.
        candidate_guids = set.union(*anchor_guid_sets)
        diagnostics["selection_strategy"] = "validated_union"
        diagnostics["candidate_guids"] = sorted(candidate_guids)

        guid_timestamps: dict[str, int] = {}
        for own_matches in anchor_matches_by_discord.values():
            for own_match in own_matches:
                guid = own_match.get("guid")
                if guid is None or guid not in candidate_guids:
                    continue
                timestamp = get_match_history_timestamp(own_match) or 0
                if guid not in guid_timestamps or timestamp < guid_timestamps[guid]:
                    guid_timestamps[guid] = timestamp

        ordered_candidate_guids: list[str] = sorted(
            candidate_guids,
            key=lambda guid: (guid_timestamps.get(guid, 0), str(guid))
        )

        # Step 2: pull the public scoreboard for every candidate game.
        scoreboard_diagnostics = {}

        async def _fetch_board(guid: str):
            scoreboard_diagnostics[guid] = {}
            async with sem:
                return guid, await fetch_public_match_data(session, guid, diagnostics=scoreboard_diagnostics[guid])

        boards_by_guid = dict(await asyncio.gather(*(_fetch_board(guid) for guid in ordered_candidate_guids)))

        # NeatQueue keeps only the final roster (a substitute replaces the player who left), so it can place the
        # linked players still listed in it; everyone else is placed by whom they played alongside in each game.
        discord_id_by_slug: dict[str, int | None] = {}

        def lookup_discord_id(slug: str) -> int | None:
            if slug not in discord_id_by_slug:
                discord_id_by_slug[slug] = get_discord_id_by_slug(slug)
            return discord_id_by_slug[slug]

        def roster_side(slug: str | None) -> int | None:
            discord_id = lookup_discord_id(slug) if slug else None
            side = neatqueue_team_index_by_discord_id.get(discord_id) if discord_id is not None else None
            return side if side is not None and side < QUEUE_TEAM_COUNT else None

        def row_identity(row: dict) -> str:
            return player_identity(row.get("slug"), (row.get("username") or "").strip())

        resolved = {guid: resolve_round_sides(board, roster_side) for guid, board in boards_by_guid.items() if board}

        # In games where both teams had linked roster players, everyone's side is certain. A game where only one
        # team had any is then kept only if the other team includes someone seen on the opposing side, so a side
        # game against strangers isn't counted, while a linked opponent replaced by an unlinked sub still is. With
        # no such game at all (e.g. nobody on one team is linked), the other playing team is trusted as before.
        confirmed_sides: dict[str, set[int]] = {}
        for guid, (sides, _reason, details) in resolved.items():
            if sides is None or "inferred_team_id" in details:
                continue
            for row in boards_by_guid[guid]:
                if parse_rank(row.get("rank")) != SURVEV_SPECTATOR_RANK and row.get("team_id") in sides:
                    confirmed_sides.setdefault(row_identity(row), set()).add(sides[row["team_id"]])
        if confirmed_sides:
            for guid, (sides, _reason, details) in list(resolved.items()):
                if sides is None or "inferred_team_id" not in details:
                    continue
                inferred_team_id = details["inferred_team_id"]
                opponents = {row_identity(row) for row in boards_by_guid[guid]
                             if row.get("team_id") == inferred_team_id
                             and parse_rank(row.get("rank")) != SURVEV_SPECTATOR_RANK}
                if not any(confirmed_sides.get(key) == {sides[inferred_team_id]} for key in opponents):
                    resolved[guid] = (None, "opponent_not_in_queue", details)

        # Step 3: aggregate per (side, survev.de player) over the accepted games. Sides are NeatQueue's own team
        # indices, so Team 1/Team 2 always match NeatQueue, and a substitute counts for the side they actually
        # played on in each game rather than whatever team_id they first appeared under.
        players: dict[tuple[int, str], dict] = {}
        selected_guids: list[str] = []
        round_winner_display_indices: list[int | None] = []
        game_rows_by_guid: dict[str, list[dict]] = {}
        for guid in ordered_candidate_guids:
            board = boards_by_guid.get(guid)
            decision = {
                "guid": guid,
                "timestamp_ms": guid_timestamps.get(guid),
                "scoreboard_requested": True,
                "scoreboard_player_count": len(board) if board else 0,
                "scoreboard_available": bool(board),
                "scoreboard_fetch": scoreboard_diagnostics.get(guid),
            }
            diagnostics["round_selection"].append(decision)
            if not board:
                decision.update({"included": False, "reason": "scoreboard_unavailable"})
                continue
            sides, reason, details = resolved[guid]
            decision.update(details)
            decision.update({"included": sides is not None, "reason": reason})
            if sides is None:
                continue

            selected_guids.append(guid)
            game_rows = game_rows_by_guid.setdefault(guid, [])
            best_rank_by_side: dict[int, int] = {}
            for p in board:
                username = (p.get("username") or "").strip()
                slug = p.get("slug")
                key = player_identity(slug, username)
                if not key:
                    continue

                parsed_rank = parse_rank(p.get("rank"))
                impact = parse_impact_score(p.get("impact_score"))
                # None for spectators and for anyone outside the queue's two teams: logged, never counted.
                side = None if parsed_rank == SURVEV_SPECTATOR_RANK else sides.get(p.get("team_id"))
                game_rows.append({
                    "identity": key,
                    "username": username,
                    "slug": slug,
                    "team_id": p.get("team_id"),
                    "rank": parsed_rank,
                    "kills": p.get("kills", 0),
                    "damage_dealt": p.get("damage_dealt", 0),
                    "impact_score": impact,
                    "display_team_index": side,
                })
                if side is None:
                    continue
                if parsed_rank is not None:
                    best_rank_by_side[side] = min(best_rank_by_side.get(side, parsed_rank), parsed_rank)

                agg = players.setdefault((side, key), {
                    "username": username, "slug": slug,
                    "games": 0, "wins": 0, "kills": 0, "damage": 0,
                    "impact_total": 0, "impact_games": 0,
                })
                agg["games"] += 1
                agg["wins"] += 1 if parsed_rank == 1 else 0
                agg["kills"] += p.get("kills", 0)
                agg["damage"] += p.get("damage_dealt", 0)
                if impact is not None:
                    agg["impact_total"] += impact
                    agg["impact_games"] += 1

            # The game goes to whichever queue team placed better (even if a lobby stranger finished above both);
            # a tie for the best placing leaves it undecided.
            best = min(best_rank_by_side.values(), default=None)
            leaders = [side for side, rank in best_rank_by_side.items() if rank == best]
            round_winner_display_indices.append(leaders[0] if len(leaders) == 1 else None)
        diagnostics["selected_guids"] = selected_guids

        if not players:
            if any(boards_by_guid.values()):
                return None, "None of the linked players' survev.de games in this queue's time window were between its two teams."
            return None, "survev.de returned no player data for this queue's match(es)."

        result_teams: list[list[dict]] = [[] for _ in range(QUEUE_TEAM_COUNT)]
        for (side, _key), p in players.items():
            discord_id = lookup_discord_id(p["slug"]) if p["slug"] else None
            result_teams[side].append({
                "discord_id": discord_id,
                "username": p["username"],
                "slug": p["slug"],
                "guest": discord_id is None,
                "stats": {
                    "games": p["games"], "wins": p["wins"], "kills": p["kills"], "damage": p["damage"],
                    # Mean over the games survev.de actually scored (None when it scored none of them).
                    "impact": round(p["impact_total"] / p["impact_games"], 1) if p["impact_games"] else None,
                },
            })

        for team in result_teams:
            team.sort(key=lambda x: x["stats"]["damage"], reverse=True)

        # Authoritative series score/winner should come from the actual round-by-round winners,
        # not from a teammate's aggregate win count. That keeps the displayed result aligned with
        # the queue's real series outcome when a team has multiple players or mixed standings.
        team_round_wins = derive_team_round_wins_from_round_winners(round_winner_display_indices, len(result_teams))
        if not any(team_round_wins):
            team_round_wins = [max((entry["stats"].get("wins", 0) for entry in team), default=0) for team in result_teams]

        winning_team_index = None
        if team_round_wins:
            max_wins = max(team_round_wins)
            if max_wins > 0 and team_round_wins.count(max_wins) == 1:
                winning_team_index = team_round_wins.index(max_wins)

        individual_games: list[dict] = []
        for game_index, guid in enumerate(selected_guids, start=1):
            players_for_game = game_rows_by_guid[guid]
            players_for_game.sort(
                key=lambda player: (
                    player.get("rank") is None,
                    player.get("rank") if player.get("rank") is not None else 999,
                    -(player.get("damage_dealt") or 0),
                )
            )
            individual_games.append({
                "game_number": game_index,
                "guid": guid,
                "winning_display_team_index": round_winner_display_indices[game_index - 1],
                "players": players_for_game,
            })

        match_history: list[bool | None] = []
        for winner_idx in round_winner_display_indices:
            if winner_idx is None or winning_team_index is None:
                match_history.append(None)
            else:
                match_history.append(winner_idx == winning_team_index)

        return {
            "teams": result_teams,
            "winning_team_index": winning_team_index,
            "match_history": match_history,
            "team_round_wins": team_round_wins,
            "total_games_played": len(selected_guids),
            "duration_ms": max(0, end_ms - start_ms),
            "match_start_ms": start_ms,
            "match_end_ms": end_ms,
            "is_final": is_final,
            "individual_games": individual_games,
            "diagnostics": diagnostics,
        }, None
