"""All network calls to the survev.de API: OAuth verification flow, match history,
timeframe stats, inventory, and market/shop data.
"""
import asyncio
import time
from datetime import datetime, timezone

import aiohttp
import discord

from bot_config import API_CONCURRENCY_LIMIT, SURVEV_CLIENT_ID, SURVEV_CLIENT_SECRET, VERIFICATION_TIMEOUT_SECONDS
from db import save_token, get_user_token, get_users_missing_slug, update_user_slug


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

    sem = asyncio.Semaphore(API_CONCURRENCY_LIMIT)

    async def _backfill_one(session: aiohttp.ClientSession, discord_id: int, access_token: str):
        async with sem:
            slug, username = await fetch_discord_link(session, access_token)
            if slug:
                update_user_slug(discord_id, slug, username)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(_backfill_one(session, did, tok) for did, tok in missing))


# Statuses that mean "survev.de is busy/hiccuping", not "the user did something wrong" — retried instead of
# ending the verification. The external API rate limit is shared by the whole bot, so a leaderboard refresh
# or queue lookup running at the same moment can briefly push verification requests into a 429.
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
DEVICE_CODE_ATTEMPTS = 3
SLUG_LOOKUP_ATTEMPTS = 3
MAX_POLL_INTERVAL_SECONDS = 30

# Verifications currently waiting on the user, keyed by Discord ID. A repeat /verify (or button click) joins
# the pending one instead of starting a second flow that would later post a misleading "timed out" message.
_active_verifications: dict[int, dict] = {}


def _retry_after_seconds(resp: aiohttp.ClientResponse, default: float) -> float:
    """Honors a Retry-After header when survev.de sends one, capped so a user is never left hanging."""
    try:
        delay = float(resp.headers.get("Retry-After", default))
    except (TypeError, ValueError):
        delay = default
    return min(max(delay, default), MAX_POLL_INTERVAL_SECONDS)


def _verification_embed(verification_uri: str, user_code: str, deadline: float) -> discord.Embed:
    expires_at = int(time.time() + max(0.0, deadline - time.monotonic()))
    return discord.Embed(
        title="🔗 Link Your survev.de Account",
        description=f"1. Click the link to verify: [**Authorize survev.de account**]({verification_uri})\n"
                    f"2. Confirm code: **`{user_code}`**\n\n"
                    f"*Waiting for authorization... (expires <t:{expires_at}:R>)*",
        color=discord.Color.blue()
    )


async def _notify_all(senders: list, **kwargs):
    """Delivers an update to every interaction waiting on this verification; one expired interaction
    shouldn't stop the others from hearing back."""
    for send_update in list(senders):
        try:
            await send_update(**kwargs)
        except discord.HTTPException as exc:
            print(f"DEBUG - verification update could not be delivered: {exc!r}")


async def _fetch_discord_link_with_retry(session: aiohttp.ClientSession, access_token: str):
    """fetch_discord_link, retried a few times — without a slug the player keeps showing up as a guest."""
    slug, username = None, None
    for attempt in range(SLUG_LOOKUP_ATTEMPTS):
        slug, username = await fetch_discord_link(session, access_token)
        if slug:
            return slug, username
        if attempt < SLUG_LOOKUP_ATTEMPTS - 1:
            await asyncio.sleep(2 * (attempt + 1))
    return slug, username


async def run_survev_verification(discord_user_id: int, send_update):
    """Runs the survev.de OAuth device-code flow, calling `send_update(**kwargs)` (a discord.py-style
    send with `content=`/`embed=`) for each step. Shared by /verify and the "not showing up?" button so
    both paths (in-channel vs DM) stay in sync.

    If the user already has a verification pending, this re-sends that same link and subscribes to its
    result rather than starting a second, competing flow."""
    active = _active_verifications.get(discord_user_id)
    if active is not None:
        active["senders"].append(send_update)
        if active["uri"] is not None:
            embed = _verification_embed(active["uri"], active["code"], active["deadline"])
            embed.set_footer(text="You already had a link pending — this is the same one, no need to start over.")
            await _notify_all([send_update], embed=embed)
        # Otherwise the link is still being requested; it will be sent to every waiting sender once ready.
        return

    # Registered before the first await so a quick double-click can't slip past the check above.
    state = {"senders": [send_update], "uri": None, "code": None, "deadline": None}
    _active_verifications[discord_user_id] = state
    try:
        await _run_verification_flow(discord_user_id, state)
    finally:
        if _active_verifications.get(discord_user_id) is state:
            _active_verifications.pop(discord_user_id, None)


async def _run_verification_flow(discord_user_id: int, state: dict):
    device_url = "https://survev.de/api/oauth/device/code"
    token_url = "https://survev.de/api/oauth/token"
    senders = state["senders"]

    payload = {
        "clientId": SURVEV_CLIENT_ID,
        "clientSecret": SURVEV_CLIENT_SECRET,
        "scope": ["read:discord", "read:stats", "read:inventory", "read:market"]
    }

    async with aiohttp.ClientSession() as session:
        data = None
        for attempt in range(DEVICE_CODE_ATTEMPTS):
            is_last_attempt = attempt == DEVICE_CODE_ATTEMPTS - 1
            delay = 2 * (attempt + 1)
            try:
                async with session.post(device_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        break
                    error_details = (await resp.text())[:500]
                    print(f"DEBUG - Survev Error Code {resp.status}: {error_details}")
                    if resp.status not in TRANSIENT_HTTP_STATUSES or is_last_attempt:
                        if resp.status == 429:
                            message = "survev.de is rate limiting the bot right now. Please try `/verify` again in a minute."
                        else:
                            message = f"Failed to start authorization. Server responded ({resp.status}): `{error_details}`"
                        await _notify_all(senders, content=message)
                        return
                    delay = _retry_after_seconds(resp, delay)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                if is_last_attempt:
                    await _notify_all(senders, content=f"Failed to contact survev.de for authorization: `{exc}`")
                    return
            await asyncio.sleep(delay)

        if not isinstance(data, dict):
            await _notify_all(senders, content="Failed to start authorization: survev.de sent an unexpected response.")
            return

        device_code = data["deviceCode"]
        user_code = data["userCode"]
        verification_uri = data["verificationUriComplete"]
        interval = max(1, int(data.get("interval", 5)))
        expires_in = min(int(data.get("expiresIn", VERIFICATION_TIMEOUT_SECONDS)), VERIFICATION_TIMEOUT_SECONDS)
        deadline = time.monotonic() + expires_in

        state["uri"], state["code"], state["deadline"] = verification_uri, user_code, deadline
        await _notify_all(senders, embed=_verification_embed(verification_uri, user_code, deadline))

        token_payload = {
            "grantType": "device_code",
            "clientId": SURVEV_CLIENT_ID,
            "clientSecret": SURVEV_CLIENT_SECRET,
            "deviceCode": device_code
        }

        extra_delay = 0.0
        while time.monotonic() < deadline:
            await asyncio.sleep(interval + extra_delay)
            extra_delay = 0.0
            try:
                async with session.post(token_url, json=token_payload) as t_resp:
                    status = t_resp.status
                    if status in TRANSIENT_HTTP_STATUSES:
                        # Rate limited / server hiccup — not the user's fault, so keep waiting for them.
                        print(f"DEBUG - verification poll for {discord_user_id} got HTTP {status}, retrying")
                        extra_delay = _retry_after_seconds(t_resp, 5)
                        continue
                    t_data = await t_resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
            if not isinstance(t_data, dict):
                continue

            if status == 200:
                access_token = t_data["accessToken"]
                slug, username = await _fetch_discord_link_with_retry(session, access_token)
                save_token(discord_user_id, access_token, slug, username)
                if slug:
                    await _notify_all(senders, content="✅ **Account Linked!**")
                else:
                    # Saved without a slug: backfill_missing_slugs fills it in on its next periodic run.
                    await _notify_all(
                        senders,
                        content="✅ **Account Linked!**\n"
                                "-# survev.de didn't return your profile just now, so match cards may show you as a "
                                "guest for a little while. The bot will fill it in automatically."
                    )
                return

            error = t_data.get("error")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                # RFC 8628: on slow_down, permanently increase the polling interval by 5 seconds.
                interval = min(interval + 5, MAX_POLL_INTERVAL_SECONDS)
            elif error == "access_denied":
                await _notify_all(senders, content="❌ Authorization was declined on survev.de. Run `/verify` again if that was a mistake.")
                return
            elif error == "expired_token":
                await _notify_all(senders, content="⌛ That code expired. Please run `/verify` again when you are ready.")
                return
            else:
                await _notify_all(senders, content=f"❌ Authorization failed: `{error or f'HTTP {status}'}`")
                return

        if get_user_token(discord_user_id):
            # e.g. an already-linked player re-ran /verify and didn't finish — their existing link still stands.
            await _notify_all(
                senders,
                content="⌛ Verification timed out, but your survev.de account is already linked, so nothing changed. "
                        "Run `/verify` again only if you want to link a different account."
            )
        else:
            await _notify_all(senders, content="⌛ Verification timed out. Please run `/verify` again when you are ready.")


async def fetch_player_matches_in_window(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int) -> list[dict]:
    """Pages through a verified player's own match history and returns the raw match objects (with guid etc.)."""
    headers = {"Authorization": f"Bearer {access_token}"}

    all_matches = []
    offset = 0
    limit = 200

    while True:
        payload = {
            "teamModeFilter": 7,  # All modes
            "from": from_ms,
            "to": to_ms,
            "count": limit,
            "offset": offset
        }

        try:
            async with session.post("https://survev.de/api/external/match_history", headers=headers, json=payload) as resp:
                if resp.status != 200:
                    break
                matches = await resp.json()
        except (aiohttp.ClientError, ValueError):
            break

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


SQUAD_TEAM_MODE = 4  # survev.de TeamMode.Squad, the mode 4v4 queues are played in


async def fetch_user_stats(session: aiohttp.ClientSession, access_token: str):
    """Fetches a verified player's all-time survev.de stats (per-mode games, rating and tier)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {"interval": "alltime", "mapIdFilter": "-1"}  # "-1" = every map
    try:
        async with session.post("https://survev.de/api/external/stats", headers=headers, json=payload) as resp:
            if resp.status == 200:
                return await resp.json(), None

            if resp.status == 401:
                return None, "Your survev.de token is invalid or revoked. Please run /verify again."
            if resp.status == 403:
                return None, "Your survev.de token does not have stats access. Please re-run /verify to grant read:stats."
            if resp.status == 429:
                return None, "survev.de is rate limiting the bot right now. Try again in a minute."

            return None, f"survev.de stats request failed ({resp.status})."
    except (aiohttp.ClientError, ValueError) as exc:
        return None, f"survev.de stats request failed: {exc}"


def extract_squad_tier(stats: dict | None) -> dict | None:
    """Pulls the Squad tier out of a /api/external/stats response as {"tier", "rating", "region"}.
    Returns None when the player is unranked (no Squad games with an impact score yet)."""
    if not isinstance(stats, dict):
        return None

    for mode in stats.get("modes") or []:
        if not isinstance(mode, dict) or mode.get("teamMode") != SQUAD_TEAM_MODE:
            continue
        tier, rating = mode.get("tier"), mode.get("rating")
        if not tier or rating is None:
            return None
        try:
            rating = int(round(float(rating)))
        except (TypeError, ValueError):
            return None
        return {"tier": str(tier), "rating": rating, "region": stats.get("primaryRegion") or None}
    return None


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
