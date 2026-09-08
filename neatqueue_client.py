"""NeatQueue API access, response parsing, and the core cross-referencing logic that
turns a NeatQueue match number + survev.de match data into one finalized result.
"""
import json
import re
from datetime import datetime, timezone

import aiohttp
import discord

from bot_config import bot, NEATQUEUE_API_BASE, NEATQUEUE_API_TOKEN, NEATQUEUE_BOT_ID, QUEUE_MATCH_FALLBACK_DURATION_MS
from db import get_guild_queue_channel, get_discord_id_by_slug, get_user_token
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

    async with session.get(url, headers=headers, params=params) as resp:
        text = await resp.text()
        if resp.status != 200:
            error_detail = text
            try:
                error_detail_json = json.loads(text)
                error_detail = error_detail_json
            except Exception:
                pass
            if resp.status == 404:
                return None, (
                    f"NeatQueue endpoint returned 404 for URL {url}. "
                    f"Verify this server has NeatQueue set up and the match number is correct; response detail: {error_detail}"
                )
            return None, f"NeatQueue API returned {resp.status} for URL {url}: {error_detail}"

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


async def get_match_end_ms(match, default_start_ms: int):
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
                return end_ms
        except (discord.HTTPException, discord.Forbidden, discord.NotFound, TypeError, ValueError, AttributeError):
            pass

    if winner_message_id:
        try:
            end_dt = discord.utils.snowflake_time(int(winner_message_id))
            end_ms = int(end_dt.timestamp() * 1000) + 5000
            if end_ms > default_start_ms:
                return end_ms
        except (TypeError, ValueError):
            pass

    end_ms = get_nested_value(match, "end_time_ms")
    parsed_end = parse_neatqueue_time(end_ms)
    if parsed_end is not None and parsed_end > default_start_ms:
        return parsed_end

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
            return latest

    return default_start_ms + QUEUE_MATCH_FALLBACK_DURATION_MS


NEATQUEUE_MATCH_ID_KEYS = ("match_id", "id", "matchId", "game_id", "gameId", "server_match_id", "game_number", "gameNumber")


def find_neatqueue_match(entries, match_id):
    match_id_str = str(match_id).lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in NEATQUEUE_MATCH_ID_KEYS:
            value = get_nested_value(entry, key)
            if value is not None and str(value).lower() == match_id_str:
                return entry
    return None


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
    """Searches the guild's configured results channel for the (possibly still in-progress) NeatQueue
    panel/winner message for this match number — used when NeatQueue's history API has no entry yet
    because the queue hasn't finished."""
    channel_id = get_guild_queue_channel(guild_id)
    if channel_id is None:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        return None

    pattern = re.compile(rf"Queue#{re.escape(str(match_id))}\b")
    try:
        async for message in channel.history(limit=200):
            if message.author.id != NEATQUEUE_BOT_ID:
                continue
            for embed in message.embeds:
                if embed.title and pattern.search(embed.title):
                    return message
    except discord.HTTPException:
        return None
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


async def resolve_queue_user_display_names(teams: list[list[dict]], client: discord.Client):
    for team in teams:
        for entry in team:
            discord_id = entry.get("discord_id")
            if discord_id is None:
                # No /verify'd Discord user owns this slug — show their real survev.de name instead.
                entry["display_name"] = entry.get("username") or "Unknown"
                continue

            member = next((m for m in client.get_all_members() if m.id == discord_id), None)
            if member is not None:
                entry["display_name"] = member.display_name
                continue

            user = client.get_user(discord_id)
            if user is None:
                try:
                    user = await client.fetch_user(discord_id)
                except Exception:
                    user = None

            if user is not None:
                entry["display_name"] = user.name
            else:
                entry["display_name"] = f"Player {discord_id}"


async def get_user_display_name(client: discord.Client, discord_id: int) -> str:
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


async def calculate_queue_match_stats(match_id: str, guild_id: int):
    """Builds the queue's teams directly from survev.de's own match data (ground truth for who played
    and which team_id they were on), then overlays a Discord display name wherever the player's slug
    matches a /verify'd user. Anyone without a linked account just keeps their survev.de username."""
    async with aiohttp.ClientSession() as session:
        nq_data, error = await fetch_neatqueue_history(session, guild_id, match_id)
        entries = extract_neatqueue_entries(nq_data) if nq_data else []
        match = entries[0] if entries else None

        # NeatQueue creates the history record early (after the first game) and does NOT keep updating
        # it as a Best-of-N series continues — only "winner" gets set once the whole series concludes.
        # So an entry existing isn't enough; only trust it once it actually has a decided winner.
        is_finished = match is not None and get_nested_value(match, "winner") is not None

        if is_finished:
            is_final = True
            # NeatQueue returns the requested match as the first entry in the result.

            start_ms = get_match_start_ms(match)
            if start_ms is None:
                return None, "NeatQueue match entry is missing a recognizable start time."

            end_ms = await get_match_end_ms(match, start_ms)

            teams = collect_neatqueue_teams(match)
            if not teams:
                return None, "NeatQueue match entry contains no player roster."
        else:
            is_final = False
            # No finished-match history yet — the queue is probably still in progress. Fall back to
            # NeatQueue's own panel/winner Discord message: its post time is the start, and "now" is
            # the end, so /queue_stats still works mid-queue instead of erroring out.
            panel_message = await find_queue_panel_message(guild_id, match_id)
            if panel_message is None:
                return None, error or "NeatQueue history contains no match entries."

            teams = None
            for embed in panel_message.embeds:
                parsed = parse_teams_from_panel_embed(embed)
                if parsed:
                    teams = parsed
                    break
            if not teams:
                return None, "Couldn't read the player roster from NeatQueue's message for this queue."

            start_ms = int(panel_message.created_at.timestamp() * 1000)
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        # Remember NeatQueue's own team ordering (by discord_id) so our survev.de-team_id-based grouping
        # can be displayed in the same left/right order NeatQueue itself uses.
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
        anchor_guid_sets = []
        anchor_matches_by_discord: dict[int, list[dict]] = {}
        for team_players in teams:
            for player in team_players:
                try:
                    discord_id = int(player.get("id"))
                except (TypeError, ValueError):
                    continue
                token = get_user_token(discord_id)
                if not token:
                    continue

                own_matches = await fetch_player_matches_in_window(session, token, start_ms, end_ms)
                guids = {m.get("guid") for m in own_matches if m.get("guid")}
                if guids:
                    anchor_guid_sets.append(guids)
                    anchor_matches_by_discord[discord_id] = own_matches

        if not anchor_guid_sets:
            return None, "No linked (/verify'd) player was found in this queue — need at least one to find the match."

        queue_guids = set.intersection(*anchor_guid_sets) if len(anchor_guid_sets) > 1 else anchor_guid_sets[0]
        if not queue_guids:
            # Anchors disagree entirely (e.g. inconsistent windowing) — union is safer than nothing.
            queue_guids = set.union(*anchor_guid_sets)

        ordered_queue_guids: list[str] = []
        seen_guids: set[str] = set()
        if anchor_matches_by_discord:
            guid_timestamps: dict[str, int] = {}
            for matches in anchor_matches_by_discord.values():
                for match in matches:
                    guid = match.get("guid")
                    if guid not in queue_guids or guid is None:
                        continue
                    timestamp = get_match_history_timestamp(match) or 0
                    if guid not in guid_timestamps or timestamp < guid_timestamps[guid]:
                        guid_timestamps[guid] = timestamp

            ordered_queue_guids = sorted(
                queue_guids,
                key=lambda guid: (guid_timestamps.get(guid, 0), str(guid))
            )
            seen_guids.update(ordered_queue_guids)

        for guid in queue_guids:
            if guid not in seen_guids:
                ordered_queue_guids.append(guid)
                seen_guids.add(guid)

        # Step 2: pull the public scoreboard for every identified round and aggregate per survev.de
        # player (keyed by slug when present, else username — covers accounts with no public slug).
        players: dict[str, dict] = {}
        # survev.de's team_id is allocated fresh per game (not stable across a series), so timeline
        # round winners must be tracked per identity here and resolved via that identity later.
        round_player_rank_by_guid: dict[str, dict[str, int]] = {}
        game_details_by_guid: dict[str, dict] = {}
        for guid in ordered_queue_guids:
            board = await fetch_public_match_data(session, guid)
            if not board:
                continue

            round_ranks = round_player_rank_by_guid.setdefault(guid, {})
            game_entry = game_details_by_guid.setdefault(guid, {"guid": guid, "players": []})
            for p in board:
                username = (p.get("username") or "").strip()
                slug = p.get("slug")
                key = slug or username.lower()
                if not key:
                    continue

                raw_rank = p.get("rank")
                parsed_rank = None
                if raw_rank is not None:
                    try:
                        parsed_rank = int(raw_rank)
                    except (TypeError, ValueError):
                        parsed_rank = None

                game_entry["players"].append({
                    "username": username,
                    "slug": slug,
                    "team_id": p.get("team_id"),
                    "rank": parsed_rank,
                    "kills": p.get("kills", 0),
                    "damage_dealt": p.get("damage_dealt", 0),
                })

                agg = players.setdefault(key, {
                    "username": username, "slug": slug, "team_id": p.get("team_id"),
                    "games": 0, "wins": 0, "kills": 0, "damage": 0, "best_rank": None
                })
                agg["games"] += 1

                agg["wins"] += 1 if parsed_rank == 1 else 0
                agg["kills"] += p.get("kills", 0)
                agg["damage"] += p.get("damage_dealt", 0)
                if parsed_rank is not None and (agg["best_rank"] is None or parsed_rank < agg["best_rank"]):
                    agg["best_rank"] = parsed_rank
                if parsed_rank is not None and (key not in round_ranks or parsed_rank < round_ranks[key]):
                    round_ranks[key] = parsed_rank

        if not players:
            return None, "survev.de returned no player data for this queue's match(es)."

        # Step 3: group by survev.de's own team_id (ground truth — no NeatQueue roster matching needed),
        # resolving a Discord identity wherever the slug belongs to a /verify'd user.
        team_ids = sorted({p["team_id"] for p in players.values() if p["team_id"] is not None})
        team_id_index = {tid: i for i, tid in enumerate(team_ids)}
        result_teams = [[] for _ in team_ids] or [[]]

        for p in players.values():
            idx = team_id_index.get(p["team_id"], 0)
            discord_id = get_discord_id_by_slug(p["slug"]) if p["slug"] else None
            result_teams[idx].append({
                "discord_id": discord_id,
                "username": p["username"],
                "slug": p["slug"],
                "guest": discord_id is None,
                "stats": {"games": p["games"], "wins": p["wins"], "kills": p["kills"], "damage": p["damage"]}
            })

        for team in result_teams:
            team.sort(key=lambda x: x["stats"]["damage"], reverse=True)

        # A game can involve more teams than just the queue's own (e.g. public matchmaking lobbies with
        # random other squads) — only show the top 2 places so unrelated teams never clutter the image.
        result_team_ids = [team_ids[i] for i in range(len(result_teams))]
        top_two_indices = set(range(len(result_teams)))
        if len(result_teams) > 2:
            team_best_rank = {}
            for tid in team_ids:
                ranks = [p["best_rank"] for p in players.values() if p["team_id"] == tid and p["best_rank"] is not None]
                team_best_rank[tid] = min(ranks) if ranks else 999
            top_two_indices = {
                team_id_index[tid]
                for tid in sorted(team_ids, key=lambda tid: team_best_rank[tid])[:2]
            }
            result_teams = [team for i, team in enumerate(result_teams) if i in top_two_indices]
            result_team_ids = [team_id for i, team_id in enumerate(result_team_ids) if i in top_two_indices]

        # Reorder to match NeatQueue's own Team 1/Team 2 display order, using known discord_ids as votes.
        def neatqueue_order_key(pair):
            team, _ = pair
            votes = [neatqueue_team_index_by_discord_id[e["discord_id"]] for e in team if e["discord_id"] in neatqueue_team_index_by_discord_id]
            if not votes:
                return len(teams)  # no known players on this team — push to the end
            return max(set(votes), key=votes.count)

        ordered_pairs = sorted(zip(result_teams, result_team_ids), key=neatqueue_order_key)
        if ordered_pairs:
            result_teams, result_team_ids = zip(*ordered_pairs)
            result_teams = [list(team) for team in result_teams]
            result_team_ids = list(result_team_ids)
        else:
            result_teams = []
            result_team_ids = []

        # Identity, not team_id, is what stays stable across a series' separate games.
        identity_to_display_index: dict[str, int] = {
            (entry["slug"] or entry["username"].lower()): idx
            for idx, team in enumerate(result_teams) for entry in team
        }
        round_winner_display_indices: list[int | None] = []
        for guid in ordered_queue_guids:
            round_ranks = round_player_rank_by_guid.get(guid, {})
            displayed_rank_one_winners = {
                identity_to_display_index[key]
                for key, rank in round_ranks.items()
                if rank == 1 and key in identity_to_display_index
            }

            # Only classify round when exactly one displayed team has rank 1 for that game.
            if len(displayed_rank_one_winners) == 1:
                round_winner_display_indices.append(next(iter(displayed_rank_one_winners)))
            else:
                round_winner_display_indices.append(None)

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
        for game_index, guid in enumerate(ordered_queue_guids, start=1):
            winner_idx = round_winner_display_indices[game_index - 1] if game_index - 1 < len(round_winner_display_indices) else None
            game_entry = game_details_by_guid.get(guid, {"guid": guid, "players": []})
            players_for_game = game_entry.get("players", [])
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
                "winning_display_team_index": winner_idx,
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
            "total_games_played": len(ordered_queue_guids),
            "duration_ms": max(0, end_ms - start_ms),
            "match_start_ms": start_ms,
            "match_end_ms": end_ms,
            "is_final": is_final,
            "individual_games": individual_games,
        }, None
