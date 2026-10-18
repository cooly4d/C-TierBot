"""Builds Discord-ready payloads (embeds/files) for leaderboard, inventory, shop,
golden fries, and compare commands by combining survev.de API data with image_utils renderers.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone

import aiohttp
import discord

from bot_config import API_CONCURRENCY_LIMIT, LEADERBOARD_BANNER_URL, LEADERBOARD_SORT_CONFIG
from db import get_all_users, get_player_queue_stats_summary, get_seasonal_queue_stats, get_user_token
from survev_client import fetch_player_timeframe_stats, fetch_user_inventory, fetch_user_market
from image_utils import (
    INVENTORY_ITEMS_PER_PAGE,
    QUEUE_IMG_ACCENT,
    SHOP_ITEMS_PER_PAGE,
    build_inventory_rarity_counts,
    compute_inventory_worth,
    generate_compare_image,
    generate_inventory_image,
    generate_shop_image,
    group_inventory_items,
    normalize_inventory_rarity_filter,
)


MAX_PAYLOAD_CACHE_ENTRIES = 200
_inventory_cache: dict[int, dict] = {}
_market_cache: dict[int, dict] = {}


def _store_cache_entry(cache: dict[int, dict], user_id: int, data: dict):
    cache.pop(user_id, None)
    cache[user_id] = data
    while len(cache) > MAX_PAYLOAD_CACHE_ENTRIES:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


async def build_inventory_image_payload(
    target_user: discord.User,
    access_token: str,
    page: int = 0,
    rarity_filter: str = "all",
    use_cache: bool = False,
):
    inventory = _inventory_cache.get(target_user.id) if use_cache else None
    if inventory is None:
        async with aiohttp.ClientSession() as session:
            inventory, error = await fetch_user_inventory(session, access_token)
            if error:
                return None, None, error, None, None, None

            if not inventory:
                return None, None, "survev.de returned an empty inventory response.", None, None, None

        _store_cache_entry(_inventory_cache, target_user.id, inventory)

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
    image_buffer = await asyncio.to_thread(generate_inventory_image, username, items, clamped_page, rarity_key)
    filename = f"inventory_{target_user.id}_p{clamped_page}.png"
    file = discord.File(image_buffer, filename=filename)
    return f"{username}'s survev.de inventory", file, None, total_pages, filename, rarity_counts


async def build_shop_image_payload(
    target_user: discord.User,
    access_token: str,
    mode: str = "all",
    page: int = 0,
    use_cache: bool = False,
):
    market_data = _market_cache.get(target_user.id) if use_cache else None
    if market_data is None:
        async with aiohttp.ClientSession() as session:
            market_data, error = await fetch_user_market(session, access_token)
            if error:
                return None, None, None, error, None

            if not market_data:
                return None, None, None, "survev.de returned an empty market response.", None

        _store_cache_entry(_market_cache, target_user.id, market_data)

    username = market_data.get("username") or str(target_user)
    offers = market_data.get("offers", [])

    if mode == "daily":
        shown_offers = [o for o in offers if o.get("slot") in (0, 1)]
    elif mode == "weekly":
        shown_offers = [o for o in offers if o.get("slot") in (2, 3)]
    else:
        shown_offers = offers

    total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
    clamped_page = max(0, min(page, total_pages - 1))

    image_buffer = await asyncio.to_thread(generate_shop_image, username, market_data, mode, clamped_page)
    filename = f"shop_{target_user.id}_{mode}_p{clamped_page}.png"
    file = discord.File(image_buffer, filename=filename)

    mode_label = "Daily" if mode == "daily" else "Weekly" if mode == "weekly" else "All"
    content = f"{username}'s {mode_label} Offers"

    return content, file, filename, None, total_pages


async def build_goldenfries_embed_payload(target_user: discord.User, access_token: str):
    async with aiohttp.ClientSession() as session:
        market_data, error = await fetch_user_market(session, access_token)
        if error:
            return None, None, error

        if not market_data:
            return None, None, "survev.de returned an empty market response."

        username = market_data.get("username") or str(target_user)
        balance = market_data.get("balance", 0)
        return username, int(balance), None


async def build_compare_payload(member_a: discord.User, member_b: discord.User):
    async with aiohttp.ClientSession() as session:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        token_a = get_user_token(member_a.id)
        token_b = get_user_token(member_b.id)

        async def _fetch_member_data(token: str | None):
            if not token:
                return None, 0
            stats, (inventory, _) = await asyncio.gather(
                fetch_player_timeframe_stats(session, token, 0, now_ms),
                fetch_user_inventory(session, token),
            )
            worth = compute_inventory_worth(inventory.get("items", [])) if isinstance(inventory, dict) else 0
            return stats, worth

        (left_stats, left_worth), (right_stats, right_worth) = await asyncio.gather(
            _fetch_member_data(token_a),
            _fetch_member_data(token_b),
        )

        left_queue_summary = get_player_queue_stats_summary(member_a.id)
        right_queue_summary = get_player_queue_stats_summary(member_b.id)

        image_buffer = await asyncio.to_thread(
            generate_compare_image,
            member_a.name,
            left_stats,
            left_worth if left_stats is not None else None,
            left_queue_summary,
            member_b.name,
            right_stats,
            right_worth if right_stats is not None else None,
            right_queue_summary,
        )
        file = discord.File(image_buffer, filename=f"compare_{member_a.id}_{member_b.id}.png")
        return f"Compare {member_a.name} vs {member_b.name}", file, None


async def generate_leaderboard_embed(period: str, days: int, sort_by: str = "kills"):
    sort_key = sort_by if sort_by in LEADERBOARD_SORT_CONFIG else "kills"
    sort_label = LEADERBOARD_SORT_CONFIG[sort_key]["label"]
    sort_emoji = LEADERBOARD_SORT_CONFIG[sort_key]["emoji"]

    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    sem = asyncio.Semaphore(API_CONCURRENCY_LIMIT)

    async def _fetch_one(session: aiohttp.ClientSession, discord_id: int, token: str):
        async with sem:
            stats = await fetch_player_timeframe_stats(session, token, from_ms, to_ms)
            if stats and stats["games"] > 0:
                return {"discord_id": discord_id, "stats": stats}
            return None

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(_fetch_one(session, did, tok) for did, tok in users))
    leaderboard_data = [entry for entry in results if entry is not None]

    leaderboard_data.sort(key=lambda x: x["stats"][sort_key], reverse=True)

    embed = discord.Embed(
        title=f"🏆 Server {period} Leaderboard",
        description=f"Performance over the past **{days} days** (Sorted by {sort_label})",
        color=discord.Color.gold() if period == "Weekly" else discord.Color.purple()
    )
    embed.set_image(url=LEADERBOARD_BANNER_URL)

    if not leaderboard_data:
        embed.description = "No matches logged by verified members in this timeframe."
        embed.set_footer(text="Data courtesy of survev.de API :)")
        return embed

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""

    for idx, entry in enumerate(leaderboard_data[:10]):  # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        stat_value = f"{stats[sort_key]:,}" if sort_key == "damage" else str(stats[sort_key])
        leaderboard_text += f"{rank} <@{entry['discord_id']}>\n{sort_emoji} {sort_label}: **{stat_value}**\n\n"

    embed.add_field(name=f"Top Players by {sort_label}", value=leaderboard_text[:1024], inline=False)
    embed.set_footer(text="Data courtesy of survev.de API :)")
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


async def build_seasonal_leaderboard_payload(guild_id: int, month: str | None = None, page: int = 0):
    """Returns (embed, month_key, page, total_pages) for one page of a month's leaderboard; page is clamped."""
    month_start, next_month_start = _parse_season_month(month)
    month_key = month_start.strftime("%Y-%m")
    month_label = month_start.strftime("%B %Y")
    stats = get_seasonal_queue_stats(
        guild_id,
        int(month_start.timestamp() * 1000),
        int(next_month_start.timestamp() * 1000),
    )

    total_pages = max(1, -(-len(stats) // SEASON_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))

    embed = discord.Embed(
        title=f"🏆 {month_label} 4v4 Avg Damage Leaderboard",
        description="Ranked by average damage per game in 4v4 queues only.",
        color=discord.Color.from_rgb(*QUEUE_IMG_ACCENT),
    )
    if not stats:
        embed.description = f"No eligible cached queue stats found for {month_label}."
        return embed, month_key, page, total_pages

    first_index = page * SEASON_PAGE_SIZE
    page_stats = stats[first_index:first_index + SEASON_PAGE_SIZE]
    rank_emojis = ["🥇", "🥈", "🥉"]
    podium_lines: list[str] = []
    table_lines: list[str] = []
    for index, entry in enumerate(page_stats, start=first_index):
        games_count = entry["games"]
        games_label = "game" if games_count == 1 else "games"
        if index < len(rank_emojis):
            podium_lines.append(
                f"{rank_emojis[index]} **#{index + 1}** <@{entry['discord_id']}>\n"
                f"┗ 💥 **`{entry['avg_damage']:,.0f}` Avg Dmg** • `{games_count:,}` {games_label}"
            )
        else:
            table_lines.append(
                f"`#{index + 1}` <@{entry['discord_id']}>\n"
                f"┗ **`{entry['avg_damage']:,.0f}` Avg Dmg** • `{games_count:,}` {games_label}"
            )

    sections: list[str] = []
    if podium_lines:
        sections.append("\n".join(podium_lines))
    if table_lines:
        sections.append("\n".join(table_lines))
    leaderboard_text = "\n────────────────────────\n".join(sections)

    field_name = "Top Players" if page == 0 else f"Ranks {first_index + 1}-{first_index + len(page_stats)}"
    embed.add_field(name=field_name, value=leaderboard_text[:1024], inline=False)
    footer = "Zero-damage queue entries are excluded."
    if total_pages > 1:
        footer = f"Page {page + 1} of {total_pages} • {footer}"
    embed.set_footer(text=footer)
    return embed, month_key, page, total_pages


async def build_leaderboard_fries_embed():
    users = get_all_users()
    if not users:
        return None, "No verified users found."

    sem = asyncio.Semaphore(API_CONCURRENCY_LIMIT)

    async def _fetch_one(session: aiohttp.ClientSession, discord_id: int, token: str):
        async with sem:
            market_data, error = await fetch_user_market(session, token)
            if error or not market_data:
                return None
            balance = market_data.get("balance")
            if balance is None:
                return None
            return {"discord_id": discord_id, "balance": balance}

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(_fetch_one(session, did, tok) for did, tok in users))
    leaderboard_data = [entry for entry in results if entry is not None]

    if not leaderboard_data:
        return None, "No Golden Fries balances could be retrieved from verified users."

    leaderboard_data.sort(key=lambda x: x["balance"], reverse=True)
    embed = discord.Embed(
        title="<:goldenfries:1535978920481136700> Server Golden Fries Leaderboard",
        description="Top verified users by their survev.de Golden Fries balance.",
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
    embed.set_footer(text="Data courtesy of survev.de API :)")
    return embed, None
