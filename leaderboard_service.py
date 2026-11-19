"""Builds Discord-ready payloads (embeds/files) for leaderboard, inventory, shop,
golden fries, and compare commands by combining survev.de API data with image_utils renderers.
"""
import asyncio
import re
import time
from collections import OrderedDict
from io import BytesIO
from datetime import datetime, timedelta, timezone

import aiohttp
import discord

from bot_config import LEADERBOARD_BANNER_URL, LEADERBOARD_SORT_CONFIG
from db import (
    get_all_users,
    get_player_queue_history,
    get_player_queue_stats_summary,
    get_seasonal_queue_stats,
    get_user_token,
)
from http_client import http_session
from survev_client import (
    extract_squad_tier,
    fetch_player_timeframe_stats,
    fetch_user_inventory,
    fetch_user_market,
    fetch_user_stats,
)
from image_utils import (
    INVENTORY_ITEMS_PER_PAGE,
    MARKET_MODE_SLOTS,
    SHOP_ITEMS_PER_PAGE,
    build_inventory_rarity_counts,
    compute_inventory_worth,
    format_duration_ms,
    generate_compare_image,
    generate_history_image,
    generate_inventory_image,
    generate_shop_image,
    group_inventory_items,
    normalize_history_metric,
    normalize_inventory_rarity_filter,
)


MAX_PAYLOAD_CACHE_ENTRIES = 200
# The survev.de external API is rate limited per OAuth app (shared by the whole bot), so tier lookups are cached.
TIER_CACHE_TTL_SECONDS = 300
# Per-player /leaderboard stats, so switching the stat or period re-sorts instead of re-fetching every player.
LEADERBOARD_CACHE_TTL_SECONDS = 300
# A player's /inventory and /market data, reused by that message's buttons for PAYLOAD_CACHE_TTL_SECONDS. After
# that a click fetches again, so an old message never shows yesterday's offers or balance as current.
PAYLOAD_CACHE_TTL_SECONDS = 300
_inventory_cache: dict[int, dict] = {}
_market_cache: dict[int, dict] = {}
_payload_cached_at: dict[tuple[int, int], float] = {}  # (id(cache), user_id) -> time.monotonic() when stored
_tier_cache: dict[int, tuple[float, dict | None]] = {}
_timeframe_cache: dict[tuple[int, int], tuple[float, dict]] = {}


# Rendered /inventory and /market pages: ("inv"|"mkt", user_id, filter, page) -> (the cached data they were drawn
# from, task giving the PNG bytes). Pages a click is likely to ask for next are rendered in the background, so a
# click usually only has to upload. A refetched inventory or shop is a new object, so its pages are drawn again.
RENDERED_PAGE_CACHE_SIZE = 96
_rendered_page_cache: OrderedDict[tuple, tuple[dict, asyncio.Task]] = OrderedDict()


def _store_payload(cache: dict[int, dict], user_id: int, data: dict) -> None:
    """Caches a player's /inventory or /market data and remembers when, so `_fresh_payload` can let it expire."""
    _store_cache_entry(cache, user_id, data)
    _payload_cached_at[(id(cache), user_id)] = time.monotonic()
    for key in [key for key in _payload_cached_at if key[0] == id(cache) and key[1] not in cache]:
        del _payload_cached_at[key]  # evicted by _store_cache_entry's size limit


def _fresh_payload(cache: dict[int, dict], user_id: int) -> dict | None:
    """The cached data if it is younger than PAYLOAD_CACHE_TTL_SECONDS, else None (and the old entry is dropped),
    so buttons on an old /market or /inventory message fetch today's offers and balance."""
    data = cache.get(user_id)
    if data is None:
        return None
    stored_at = _payload_cached_at.get((id(cache), user_id))
    if stored_at is not None and time.monotonic() - stored_at >= PAYLOAD_CACHE_TTL_SECONDS:
        cache.pop(user_id, None)
        _payload_cached_at.pop((id(cache), user_id), None)
        return None
    return data


def has_cached_inventory(user_id: int) -> bool:
    return _fresh_payload(_inventory_cache, user_id) is not None


def has_cached_market(user_id: int) -> bool:
    return _fresh_payload(_market_cache, user_id) is not None


def _png_bytes(render, *args) -> bytes:
    return render(*args).getvalue()


def _rendered_page_task(key: tuple, source: dict, render, *args) -> asyncio.Task:
    """The (possibly still running) render for `key`, started now unless it is cached for this `source` object."""
    cached = _rendered_page_cache.get(key)
    if cached is not None and cached[0] is source:
        task = cached[1]
        if not task.done() or (not task.cancelled() and task.exception() is None):
            _rendered_page_cache.move_to_end(key)
            return task
    task = asyncio.create_task(asyncio.to_thread(_png_bytes, render, *args))
    # Background renders nobody awaits must not log "exception was never retrieved".
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    _rendered_page_cache[key] = (source, task)
    _rendered_page_cache.move_to_end(key)
    while len(_rendered_page_cache) > RENDERED_PAGE_CACHE_SIZE:
        _rendered_page_cache.popitem(last=False)
    return task


def _inventory_page_task(user_id: int, inventory: dict, username: str, items: list[dict], page: int,
                         rarity_key: str) -> asyncio.Task:
    return _rendered_page_task(("inv", user_id, rarity_key, page), inventory,
                               generate_inventory_image, username, items, page, rarity_key)


def _market_page_task(user_id: int, market_data: dict, username: str, mode: str, page: int) -> asyncio.Task:
    return _rendered_page_task(("mkt", user_id, mode, page), market_data,
                               generate_shop_image, username, market_data, mode, page)


def _market_total_pages(market_data: dict, mode: str) -> int:
    offers = market_data.get("offers", [])
    if mode in MARKET_MODE_SLOTS:
        offers = [o for o in offers if o.get("slot") in MARKET_MODE_SLOTS[mode]]
    return max(1, -(-len(offers) // SHOP_ITEMS_PER_PAGE))


def _store_cache_entry(cache: dict[int, dict], user_id: int, data: dict):
    cache.pop(user_id, None)
    cache[user_id] = data
    while len(cache) > MAX_PAYLOAD_CACHE_ENTRIES:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


async def get_squad_tier(discord_id: int, access_token: str, session: aiohttp.ClientSession | None = None):
    """Returns (tier, error) for a verified player's survev.de Squad tier. tier is None with no error when the
    player is unranked. Only successful lookups are cached, so a rate-limit hiccup doesn't stick."""
    cached = _tier_cache.get(discord_id)
    if cached is not None and time.monotonic() - cached[0] < TIER_CACHE_TTL_SECONDS:
        return cached[1], None

    stats, error = await fetch_user_stats(session or http_session, access_token)
    if error:
        return None, error

    tier = extract_squad_tier(stats)
    _store_cache_entry(_tier_cache, discord_id, (time.monotonic(), tier))
    return tier, None


async def build_inventory_image_payload(
    target_user: discord.User,
    access_token: str,
    page: int = 0,
    rarity_filter: str = "all",
    use_cache: bool = False,
):
    inventory = _fresh_payload(_inventory_cache, target_user.id) if use_cache else None
    if inventory is None:
        inventory, error = await fetch_user_inventory(http_session, access_token)
        if error:
            return None, None, error, None, None, None

        if not inventory:
            return None, None, "survev.de returned an empty inventory response.", None, None, None

        _store_payload(_inventory_cache, target_user.id, inventory)

    username = inventory.get("username") or str(target_user)
    items = inventory.get("items", [])
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)
    grouped_items = group_inventory_items(items)
    rarity_counts = build_inventory_rarity_counts(grouped_items)
    if rarity_key != "all":
        rarity_value = int(rarity_key)
        grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
    total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
    clamped_page = max(0, min(page, total_pages - 1))
    png = await asyncio.shield(
        _inventory_page_task(target_user.id, inventory, username, items, clamped_page, rarity_key)
    )
    for neighbour in (clamped_page + 1, clamped_page - 1):
        if 0 <= neighbour < total_pages:
            _inventory_page_task(target_user.id, inventory, username, items, neighbour, rarity_key)
    filename = f"inventory_{target_user.id}_p{clamped_page}.png"
    file = discord.File(BytesIO(png), filename=filename)
    return f"{username}'s survev.de inventory", file, None, total_pages, filename, rarity_counts


async def build_shop_image_payload(
    target_user: discord.User,
    access_token: str,
    mode: str = "all",
    page: int = 0,
    use_cache: bool = False,
):
    market_data = _fresh_payload(_market_cache, target_user.id) if use_cache else None
    if market_data is None:
        market_data, error = await fetch_user_market(http_session, access_token)
        if error:
            return None, None, None, error, None

        if not market_data:
            return None, None, None, "survev.de returned an empty market response.", None

        _store_payload(_market_cache, target_user.id, market_data)

    username = market_data.get("username") or str(target_user)
    total_pages = _market_total_pages(market_data, mode)
    clamped_page = max(0, min(page, total_pages - 1))

    png = await asyncio.shield(_market_page_task(target_user.id, market_data, username, mode, clamped_page))
    # Get the other modes' first pages and this mode's neighbours ready for the next click.
    for other_mode in ("daily", "weekly", "all"):
        if other_mode != mode:
            _market_page_task(target_user.id, market_data, username, other_mode, 0)
    for neighbour in (clamped_page + 1, clamped_page - 1):
        if 0 <= neighbour < total_pages:
            _market_page_task(target_user.id, market_data, username, mode, neighbour)
    filename = f"shop_{target_user.id}_{mode}_p{clamped_page}.png"
    file = discord.File(BytesIO(png), filename=filename)

    content = f"{username}'s {mode} offers" if mode in MARKET_MODE_SLOTS else f"{username}'s offers"

    return content, file, filename, None, total_pages


async def build_goldenfries_embed_payload(target_user: discord.User, access_token: str):
    market_data, error = await fetch_user_market(http_session, access_token)
    if error:
        return None, None, error

    if not market_data:
        return None, None, "survev.de returned an empty market response."

    username = market_data.get("username") or str(target_user)
    balance = market_data.get("balance", 0)
    return username, int(balance), None


async def build_compare_payload(member_a: discord.User, member_b: discord.User):
    session = http_session
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    token_a = get_user_token(member_a.id)
    token_b = get_user_token(member_b.id)

    async def _fetch_member_data(member: discord.User, token: str | None):
        """(stats, worth, tier) for one player. stats is None when they aren't linked and {"unavailable": True}
        when their match history couldn't be read in full (a rate limit mid-way would otherwise count as zeros);
        worth is None when the inventory couldn't be read."""
        if not token:
            return None, None, None
        history_diagnostics: dict = {}
        stats, (inventory, inventory_error), (tier, tier_error) = await asyncio.gather(
            fetch_player_timeframe_stats(session, token, 0, now_ms, diagnostics=history_diagnostics),
            fetch_user_inventory(session, token),
            get_squad_tier(member.id, token, session),
        )
        if history_diagnostics.get("pagination_complete") is False:
            stats = {"unavailable": True}
        worth = None
        items = inventory.get("items") if not inventory_error and isinstance(inventory, dict) else None
        if isinstance(items, list):  # a response without an item list is a failed lookup, not an empty inventory
            worth = compute_inventory_worth(items)
        return stats, worth, ({"unavailable": True} if tier_error else tier)

    (left_stats, left_worth, left_tier), (right_stats, right_worth, right_tier) = await asyncio.gather(
        _fetch_member_data(member_a, token_a),
        _fetch_member_data(member_b, token_b),
    )

    left_queue_summary = get_player_queue_stats_summary(member_a.id)
    right_queue_summary = get_player_queue_stats_summary(member_b.id)
    left_name, right_name = _member_name(member_a), _member_name(member_b)

    image_buffer = await asyncio.to_thread(
        generate_compare_image,
        left_name,
        left_stats,
        left_worth,
        left_queue_summary,
        right_name,
        right_stats,
        right_worth,
        right_queue_summary,
        left_tier,
        right_tier,
    )
    file = discord.File(image_buffer, filename=f"compare_{member_a.id}_{member_b.id}.png")
    return f"Compare {left_name} vs {right_name}", file, None


def _member_name(member: discord.abc.User) -> str:
    """The name people see in this server (nickname, then global display name, then username)."""
    return getattr(member, "display_name", None) or getattr(member, "name", None) or str(member)


def _bold_name(member: discord.abc.User) -> str:
    """The member's name in bold, for messages that talk about someone without pinging them (a nickname such as
    "@everyone" is escaped too)."""
    return f"**{discord.utils.escape_mentions(discord.utils.escape_markdown(_member_name(member)))}**"


def link_prompt(target: discord.abc.User, viewer_id: int | None) -> str:
    """What to say when `target` hasn't linked a survev.de account: who it is, and who can fix it."""
    if viewer_id is not None and target.id == viewer_id:
        return "You haven't linked a survev.de account yet. Run /verify to link one."
    return f"{_bold_name(target)} hasn't linked a survev.de account yet. They can run /verify to link one."


def survev_error_for(error: str, target: discord.abc.User, viewer_id: int | None) -> str:
    """A survev.de error (written for the account's owner) reworded when someone else is looking: an expired or
    narrowed link is the owner's to fix, so it names them instead of saying "Your". Other errors pass through."""
    status = getattr(error, "status", None)
    if viewer_id is None or target.id == viewer_id or status not in (401, 403):
        return str(error)
    if status == 401:
        return f"{_bold_name(target)}'s survev.de link has expired. They can run /verify to link it again."
    return f"{_bold_name(target)}'s survev.de link doesn't let the bot read this. They can run /verify again to allow it."


def format_squad_tier_field(tier: dict | None, error: str | None) -> str:
    """Embed field text for a player's survev.de Squad tier. A failed lookup says so in a few words; the raw error is
    about survev.de or the token, which this line isn't the place to explain."""
    if error:
        return "unavailable right now"
    if tier is None:
        return "Unranked"
    parts = [f"**{tier['tier']}**", f"rating {tier['rating']}"]
    if tier.get("region"):
        parts.append(tier["region"].upper())
    return " · ".join(parts)


PROFILE_HISTORY_LIMIT = 10


def _history_alt_text(display_name: str, rows: list[dict]) -> str:
    """Text alternative for the history card (Discord caps attachment descriptions at 1024 characters)."""
    alt_lines = [f"Last {len(rows)} queues for {display_name}:"]
    for r in rows:
        won_str = "Won" if r.get("won") is True else "Lost" if r.get("won") is False else "Undecided"
        kills_str = f"{r.get('kills')} kills" if r.get("kills") is not None else ""
        dmg_str = f"{r.get('avg_damage', 0):,.0f} avg dmg"
        imp_str = f"impact {int(r['impact'] + 0.5)}" if r.get("impact") is not None else ""
        parts = [p for p in (kills_str, dmg_str, imp_str) if p]
        alt_lines.append(f"Queue #{r['match_id']} ({won_str}): {', '.join(parts)}")
    return " ".join(alt_lines)[:1000]


def _profile_number(value) -> str:
    return f"**{value:,.0f}**" if value is not None else "N/A"


async def build_profile_overview(target: discord.abc.User, access_token: str | None, guild_id: int | None = None,
                                 viewer_id: int | None = None):
    """/profile's Overview: the player's queue averages and Squad tier as a few short lines. `viewer_id` is who asked,
    so an empty profile can say "You" or name the player.

    Returns (embed, has_history, None), where has_history says whether this server has a history card for them, or
    (None, False, message) when there is nothing to show for this player yet."""
    summary = get_player_queue_stats_summary(target.id)
    tier, tier_error = (None, None)
    if access_token:
        tier, tier_error = await get_squad_tier(target.id, access_token)
    has_history = guild_id is not None and bool(get_player_queue_history(guild_id, target.id, limit=1))

    has_queues = summary["matches"] > 0
    if not has_queues and tier is None and not has_history:
        if not access_token:
            return None, False, link_prompt(target, viewer_id)
        if getattr(tier_error, "status", None) in (401, 403):  # the link itself is the problem
            return None, False, survev_error_for(tier_error, target, viewer_id)
        if viewer_id is not None and target.id == viewer_id:
            return None, False, "You haven't finished a 4v4 queue the bot has recorded yet."
        return None, False, f"{_bold_name(target)} hasn't finished a 4v4 queue the bot has recorded yet."

    lines = []
    if has_queues:
        month_name = datetime.now(timezone.utc).strftime("%B")
        monthly_avg = summary["monthly_avg_damage"]
        monthly_impact = summary.get("monthly_avg_impact")
        if monthly_avg is None:
            monthly_damage_text = monthly_impact_text = "no queues yet"
        else:
            monthly_damage_text = _profile_number(monthly_avg)
            monthly_impact_text = _profile_number(monthly_impact)
        lines += [
            f"⏱️ **{format_duration_ms(summary['total_duration_ms'])}** played in 4v4 queues",
            f"💥 Avg damage {_profile_number(summary['all_time_avg_damage'])} · {month_name} {monthly_damage_text}",
            f"⚡ Avg impact {_profile_number(summary.get('all_time_avg_impact'))} · {month_name} {monthly_impact_text}",
        ]
    else:
        lines.append("No 4v4 queues recorded yet.")
    if access_token:
        tier_text = format_squad_tier_field(tier, tier_error)
        if getattr(tier_error, "status", None) in (401, 403):
            tier_text += f": {survev_error_for(tier_error, target, viewer_id)}"
        lines.append(f"🏅 Squad tier {tier_text}")

    embed = discord.Embed(
        title=f"{target.display_name}'s NeatQueue stats",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="NeatQueue 4v4s")
    return embed, has_history, None


async def build_profile_history(target: discord.abc.User, guild_id: int | None, metric: str = "damage",
                                viewer_id: int | None = None):
    """/profile's History: the card with the player's last 10 queues in this server, its trend chart showing `metric`.
    The card carries the player's name, so the embed has no title.

    Returns (embed, file, metric, None), or (None, None, metric, message) when there is no history to show."""
    metric = normalize_history_metric(metric)
    rows = get_player_queue_history(guild_id, target.id, limit=PROFILE_HISTORY_LIMIT) if guild_id is not None else []
    if not rows:
        if viewer_id is not None and target.id == viewer_id:
            return None, None, metric, "You haven't finished a 4v4 queue in this server yet."
        return None, None, metric, f"{_bold_name(target)} hasn't finished a 4v4 queue in this server yet."

    image_buffer = await asyncio.to_thread(generate_history_image, target.display_name, rows, metric)
    filename = f"history_{guild_id}_{target.id}_{metric}.png"
    file = discord.File(image_buffer, filename=filename, description=_history_alt_text(target.display_name, rows))
    embed = discord.Embed(color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text="NeatQueue 4v4s · last 10 finished queues in this server")
    return embed, file, metric, None


async def _cached_timeframe_stats(discord_id: int, token: str, days: int, from_ms: int, to_ms: int) -> dict | None:
    """One player's /leaderboard stats for the last `days` days, reused for LEADERBOARD_CACHE_TTL_SECONDS.
    Only complete fetches are cached, so a player missed by a rate limit is retried on the next click."""
    now = time.monotonic()
    cached = _timeframe_cache.get((discord_id, days))
    if cached is not None and now - cached[0] < LEADERBOARD_CACHE_TTL_SECONDS:
        return cached[1]

    fetch_diagnostics: dict = {}
    stats = await fetch_player_timeframe_stats(http_session, token, from_ms, to_ms, diagnostics=fetch_diagnostics)
    if fetch_diagnostics.get("pagination_complete"):
        for key, (cached_at, _) in list(_timeframe_cache.items()):
            if now - cached_at >= LEADERBOARD_CACHE_TTL_SECONDS:
                del _timeframe_cache[key]
        _timeframe_cache[(discord_id, days)] = (now, stats)
    return stats


async def generate_leaderboard_embed(period: str, days: int, sort_by: str = "kills"):
    sort_key = sort_by if sort_by in LEADERBOARD_SORT_CONFIG else "kills"
    sort_label = LEADERBOARD_SORT_CONFIG[sort_key]["label"]
    sort_emoji = LEADERBOARD_SORT_CONFIG[sort_key]["emoji"]

    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    results = await asyncio.gather(*(_cached_timeframe_stats(did, tok, days, from_ms, to_ms) for did, tok in users))
    leaderboard_data = [
        {"discord_id": discord_id, "stats": stats}
        for (discord_id, _token), stats in zip(users, results)
        if stats and stats["games"] > 0
    ]

    leaderboard_data.sort(key=lambda x: x["stats"][sort_key], reverse=True)

    embed = discord.Embed(
        title=f"{period} server leaderboard",  # the button router reads "Weekly"/"Monthly" here
        description=f"Performance over the past **{days} days** (Sorted by {sort_label})",
        color=discord.Color.gold() if period == "Weekly" else discord.Color.purple()
    )
    embed.set_image(url=LEADERBOARD_BANNER_URL)

    if not leaderboard_data:
        embed.description = "No matches logged by linked members in this timeframe."
        return embed

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""

    for idx, entry in enumerate(leaderboard_data[:10]):  # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        stat_value = f"{stats[sort_key]:,}" if sort_key == "damage" else str(stats[sort_key])
        leaderboard_text += f"{rank} <@{entry['discord_id']}>\n{sort_emoji} {sort_label}: **{stat_value}**\n\n"

    embed.add_field(name=f"Top Players by {sort_label}", value=leaderboard_text[:1024], inline=False)
    return embed


def _parse_season_month(month: str | None) -> tuple[datetime, datetime]:
    if month is None:
        now = datetime.now(timezone.utc)
        year, month_number = now.year, now.month
    else:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("Month must use YYYY-MM format, for example 2026-09.")
        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError("That calendar month is not valid.") from exc
        year, month_number = parsed_month.year, parsed_month.month

    start = datetime(year, month_number, 1, tzinfo=timezone.utc)
    if month_number == 12:
        next_start = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_start = datetime(year, month_number + 1, 1, tzinfo=timezone.utc)
    return start, next_start


SEASON_PAGE_SIZE = 10  # keeps one page inside the 1024-character embed field limit

# /leaderboard_season stat picker: key -> (label, emoji, description, value format). "damage" is the default.
SEASON_STATS = {
    "damage": ("Avg Damage", "💥", "avg dmg", "{:,.0f}"),
    "impact": ("Avg Impact", "⚡", "average survev.de impact score per game", "{:,.0f}"),
    "kills": ("Avg Kills", "⚔️", "average kills per game", "{:,.1f}"),
}


def normalize_season_stat(stat: str | None) -> str:
    return stat if stat in SEASON_STATS else "damage"


async def build_seasonal_leaderboard_payload(guild_id: int, month: str | None = None, page: int = 0, stat: str = "damage"):
    """Returns (embed, month_key, page, total_pages) for one page of a month's leaderboard ranked by `stat`
    (see SEASON_STATS); page is clamped."""
    stat = normalize_season_stat(stat)
    label, emoji, description, value_format = SEASON_STATS[stat]
    month_start, next_month_start = _parse_season_month(month)
    month_key = month_start.strftime("%Y-%m")
    month_label = month_start.strftime("%B %Y")
    stats = get_seasonal_queue_stats(
        guild_id,
        int(month_start.timestamp() * 1000),
        int(next_month_start.timestamp() * 1000),
        stat,
    )

    total_pages = max(1, -(-len(stats) // SEASON_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    embed = discord.Embed(
        title=f"{month_label} 4v4 leaderboard · {label.lower().replace('damage', 'dmg')}",
        description=f"Ranked by {description} in 4v4 queues only.",
        color=discord.Color.gold(),
    )
    if not stats:
        embed.description = f"No eligible cached queue stats found for {month_label}."
        return embed, month_key, page, total_pages

    first_index = page * SEASON_PAGE_SIZE
    page_stats = stats[first_index:first_index + SEASON_PAGE_SIZE]
    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""
    for index, entry in enumerate(page_stats, start=first_index):
        rank = rank_emojis[index] if index < len(rank_emojis) else f"`#{index + 1}`"
        leaderboard_text += (
            f"{rank} <@{entry['discord_id']}>\n"
            f"{emoji} {label}: **{value_format.format(entry['value'])}** "
            f"({entry['games']:,} games)\n\n"
        )

    field_name = "Top Players" if page == 0 else f"Ranks {first_index + 1}-{first_index + len(page_stats)}"
    embed.add_field(name=field_name, value=leaderboard_text[:1024], inline=False)
    footer = "Zero-damage queue entries are excluded."
    if stat == "impact":
        footer = "Only queues survev.de scored for impact count. " + footer
    if total_pages > 1:
        footer = f"Page {page + 1} of {total_pages} • {footer}"
    embed.set_footer(text=footer)
    return embed, month_key, page, total_pages


async def build_leaderboard_fries_embed():
    users = get_all_users()
    if not users:
        return None, "No linked players found."

    async def _fetch_one(discord_id: int, token: str):
        market_data, error = await fetch_user_market(http_session, token)
        if error or not market_data:
            return None
        balance = market_data.get("balance")
        if balance is None:
            return None
        return {"discord_id": discord_id, "balance": balance}

    results = await asyncio.gather(*(_fetch_one(did, tok) for did, tok in users))
    leaderboard_data = [entry for entry in results if entry is not None]

    if not leaderboard_data:
        return None, "No Golden Fries balances could be retrieved for linked players."

    leaderboard_data.sort(key=lambda x: x["balance"], reverse=True)
    embed = discord.Embed(
        title="<:goldenfries:1535978920481136700> Server Golden Fries leaderboard",
        description="Top linked players by their survev.de Golden Fries balance.",
        color=discord.Color.gold()
    )

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""
    for idx, entry in enumerate(leaderboard_data[:10]):
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        leaderboard_text += (
            f"{rank} <@{entry['discord_id']}>\n"
            f"🍟 Balance: **{entry['balance']:,}**\n\n"
        )

    embed.add_field(name="Top Golden Fries", value=leaderboard_text[:1024], inline=False)
    return embed, None
