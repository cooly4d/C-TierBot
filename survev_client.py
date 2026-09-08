"""All network calls to the survev.de API: OAuth verification flow, match history,
timeframe stats, inventory, and market/shop data.
"""
import asyncio
from datetime import datetime, timezone

import aiohttp
import discord

from bot_config import SURVEV_CLIENT_ID, SURVEV_CLIENT_SECRET
from db import save_token, get_users_missing_slug, update_user_slug


async def fetch_discord_link(session: aiohttp.ClientSession, access_token: str):
    """Returns (slug, username) for a survev.de access token, or (None, None) on failure."""
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.post("https://survev.de/api/external/discord_link", headers=headers) as resp:
            if resp.status != 200:
                return None, None
            data = await resp.json()
            return data.get("slug"), data.get("username")
    except aiohttp.ClientError:
        return None, None


async def backfill_missing_slugs():
    """One-time-ish catch-up: fills in slug/username for users who verified before we started storing it,
    using their access token we already have — no need for them to /verify again."""
    missing = get_users_missing_slug()
    if not missing:
        return

    async with aiohttp.ClientSession() as session:
        for discord_id, access_token in missing:
            slug, username = await fetch_discord_link(session, access_token)
            if slug:
                update_user_slug(discord_id, slug, username)


async def run_survev_verification(discord_user_id: int, send_update):
    """Runs the survev.de OAuth device-code flow, calling `send_update(**kwargs)` (a discord.py-style
    send with `content=`/`embed=`) for each step. Shared by /verify and the "not showing up?" button so
    both paths (in-channel vs DM) stay in sync."""
    device_url = "https://survev.de/api/oauth/device/code"
    token_url = "https://survev.de/api/oauth/token"

    payload = {
        "clientId": SURVEV_CLIENT_ID,
        "clientSecret": SURVEV_CLIENT_SECRET,
        "scope": ["read:discord", "read:stats", "read:inventory", "read:market"]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(device_url, json=payload) as resp:
            if resp.status != 200:
                error_details = await resp.text()
                print(f"DEBUG - Survev Error Code {resp.status}: {error_details}")
                await send_update(content=f"Failed to start authorization. Server responded ({resp.status}): `{error_details}`")
                return
            data = await resp.json()

            device_code = data["deviceCode"]
            user_code = data["userCode"]
            verification_uri = data["verificationUriComplete"]
            interval = data.get("interval", 5)

            embed = discord.Embed(
                title="🔗 Link Your survev.de Account",
                description=f"1. Click the link to verify: [**Authorize survev.de account**]({verification_uri})\n"
                            f"2. Confirm code: **`{user_code}`**\n\n"
                            f"*Waiting for authorization...*",
                color=discord.Color.blue()
            )
            await send_update(embed=embed)

            token_payload = {
                "grantType": "device_code",
                "clientId": SURVEV_CLIENT_ID,
                "clientSecret": SURVEV_CLIENT_SECRET,
                "deviceCode": device_code
            }

            while True:
                await asyncio.sleep(interval)
                async with session.post(token_url, json=token_payload) as t_resp:
                    t_data = await t_resp.json()

                    if t_resp.status == 200:
                        access_token = t_data["accessToken"]
                        slug, username = await fetch_discord_link(session, access_token)
                        save_token(discord_user_id, access_token, slug, username)
                        await send_update(content="✅ **Account Linked!** You are now entered into weekly & monthly leaderboards.")
                        break

                    error = t_data.get("error")
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        await asyncio.sleep(2)
                    else:
                        await send_update(content=f"❌ Authorization failed: `{error}`")
                        break


async def fetch_player_matches_in_window(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int) -> list[dict]:
    """Pages through a verified player's own match history and returns the raw match objects (with guid etc.)."""
    headers = {"Authorization": f"Bearer {access_token}"}

    all_matches = []
    offset = 0
    limit = 200

    while True:
        payload = {
            "teamModeFilter": 7, # All modes
            "from": from_ms,
            "to": to_ms,
            "count": limit,
            "offset": offset
        }

        async with session.post("https://survev.de/api/external/match_history", headers=headers, json=payload) as resp:
            if resp.status != 200:
                break
            matches = await resp.json()

        # Stop if no matches are returned
        if not matches or not isinstance(matches, list):
            break

        all_matches.extend(matches)

        # Stop if we received less than the maximum request count (we reached the last page)
        if len(matches) < limit:
            break

        # Move to the next page
        offset += limit

    return all_matches


def get_match_history_timestamp(match: dict) -> int | None:
    if not isinstance(match, dict):
        return None
    # "end_time" (e.g. "2026-08-07T15:20:37.255Z") is the real field survev.de sends; the rest are fallbacks.
    for key in ("end_time", "end_time_ms", "start_time_ms", "start_time", "time", "timestamp", "created_at", "createdAt"):
        value = match.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    # fromisoformat can't parse a trailing "Z" (pre-3.11) — normalize to an explicit offset first.
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return int(parsed.timestamp() * 1000)
                except ValueError:
                    continue
    return None


async def fetch_player_timeframe_stats(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int):
    """Aggregates a verified player's own matches within the timeframe. Used by the leaderboard."""
    all_matches = await fetch_player_matches_in_window(session, access_token, from_ms, to_ms)

    if not all_matches:
        return {"games": 0, "wins": 0, "kills": 0, "damage": 0}

    return {
        "games": len(all_matches),
        "wins": sum(1 for m in all_matches if m.get("rank") == 1),
        "kills": sum(m.get("kills", 0) for m in all_matches),
        "damage": sum(m.get("damage_dealt", 0) for m in all_matches)
    }


async def fetch_user_inventory(session: aiohttp.ClientSession, access_token: str):
    """Fetches a survev.de user's inventory with their OAuth access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with session.post("https://survev.de/api/external/inventory", headers=headers) as resp:
            if resp.status == 200:
                return await resp.json(), None

            if resp.status == 401:
                return None, "Your survev.de token is invalid or revoked. Please run /verify again."
            if resp.status == 403:
                return None, "Your survev.de token does not have inventory access. Please re-run /verify to grant read:inventory."

            text = await resp.text()
            return None, f"survev.de inventory request failed ({resp.status}): {text}"
    except aiohttp.ClientError as exc:
        return None, f"survev.de inventory request failed: {exc}"


async def fetch_user_market(session: aiohttp.ClientSession, access_token: str):
    """Fetches a survev.de user's current market/shop data with their OAuth access token."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with session.post("https://survev.de/api/external/market", headers=headers) as resp:
            if resp.status == 200:
                return await resp.json(), None

            if resp.status == 401:
                return None, "Your survev.de token is invalid or revoked. Please run /verify again."
            if resp.status == 403:
                return None, "Your survev.de token does not have market access. Please re-run /verify to grant read:market."

            text = await resp.text()
            return None, f"survev.de market request failed ({resp.status}): {text}"
    except aiohttp.ClientError as exc:
        return None, f"survev.de market request failed: {exc}"


async def fetch_public_match_data(session: aiohttp.ClientSession, guid: str) -> list[dict] | None:
    """Public per-match scoreboard (no auth needed) — every player in that one game, including guests
    with no survev.de account at all (they still show up with a username, just slug=None)."""
    try:
        async with session.post("https://survev.de/api/match_data", json={"gameId": guid}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except aiohttp.ClientError:
        return None
    return data if isinstance(data, list) else None
