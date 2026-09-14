"""Builds Discord-ready payloads (embeds/files) for leaderboard, inventory, shop,
golden fries, and compare commands by combining survev.de API data with image_utils renderers.
"""
from datetime import datetime, timedelta, timezone

import aiohttp
import discord

from bot_config import LEADERBOARD_BANNER_URL, LEADERBOARD_SORT_CONFIG
from db import get_all_users, get_player_queue_stats_summary, get_user_token
from survev_client import fetch_player_timeframe_stats, fetch_user_inventory, fetch_user_market
from image_utils import (
    INVENTORY_ITEMS_PER_PAGE,
    SHOP_ITEMS_PER_PAGE,
    build_inventory_rarity_counts,
    compute_inventory_worth,
    generate_compare_image,
    generate_goldenfries_image,
    generate_inventory_image,
    generate_leaderboard_fries_image,
    generate_leaderboard_image,
    generate_shop_image,
    group_inventory_items,
    normalize_inventory_rarity_filter,
)


def build_inventory_image_payload(target_user: discord.User, access_token: str, page: int = 0, rarity_filter: str = "all"):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            inventory, error = await fetch_user_inventory(session, access_token)
            if error:
                return None, None, error, None, None, None

            if not inventory:
                return None, None, "survev.de returned an empty inventory response.", None, None, None

            username = inventory.get("username") or str(target_user)
            items = inventory.get("items", [])
            rarity_key = normalize_inventory_rarity_filter(rarity_filter)
            grouped_items = group_inventory_items(items)
            rarity_counts = build_inventory_rarity_counts(grouped_items)
            if rarity_key != "all":
                rarity_value = int(rarity_key)
                grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
            total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
            image_buffer = generate_inventory_image(username, items, page, rarity_key)
            filename = f"inventory_{target_user.id}_p{page}.png"
            file = discord.File(image_buffer, filename=filename)
            return f"{username}'s survev.de inventory", file, None, total_pages, filename, rarity_counts

    return wrapper()


def build_shop_image_payload(target_user: discord.User, access_token: str, mode: str = "all", page: int = 0):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            market_data, error = await fetch_user_market(session, access_token)
            if error:
                return None, None, None, error, None

            if not market_data:
                return None, None, None, "survev.de returned an empty market response.", None

            username = market_data.get("username") or str(target_user)
            offers = market_data.get("offers", [])
            
            # Filter by mode
            if mode == "daily":
                shown_offers = [o for o in offers if o.get("slot") in (0, 1)]
            elif mode == "weekly":
                shown_offers = [o for o in offers if o.get("slot") in (2, 3)]
            else:
                shown_offers = offers
            
            total_pages = max(1, -(-len(shown_offers) // SHOP_ITEMS_PER_PAGE))
            
            # Clamp page
            clamped_page = max(0, min(page, total_pages - 1))
            
            image_buffer = generate_shop_image(username, market_data, mode, clamped_page)
            filename = f"shop_{target_user.id}_{mode}_p{clamped_page}.png"
            file = discord.File(image_buffer, filename=filename)
            
            mode_label = "Daily" if mode == "daily" else "Weekly" if mode == "weekly" else "All"
            content = f"{username}'s {mode_label} Offers"
            
            return content, file, filename, None, total_pages

    return wrapper()


def build_goldenfries_embed_payload(target_user: discord.User, access_token: str):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            market_data, error = await fetch_user_market(session, access_token)
            if error:
                return None, None, error

            if not market_data:
                return None, None, "survev.de returned an empty market response."

            username = market_data.get("username") or str(target_user)
            balance = market_data.get("balance", 0)
            return username, int(balance), None

    return wrapper()


def build_compare_payload(member_a: discord.User, member_b: discord.User):
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            token_a = get_user_token(member_a.id)
            token_b = get_user_token(member_b.id)

            left_stats = None
            left_worth = 0
            if token_a:
                left_stats = await fetch_player_timeframe_stats(session, token_a, 0, now_ms)
                inventory_a, _ = await fetch_user_inventory(session, token_a)
                if inventory_a and isinstance(inventory_a, dict):
                    left_worth = compute_inventory_worth(inventory_a.get("items", []))

            right_stats = None
            right_worth = 0
            if token_b:
                right_stats = await fetch_player_timeframe_stats(session, token_b, 0, now_ms)
                inventory_b, _ = await fetch_user_inventory(session, token_b)
                if inventory_b and isinstance(inventory_b, dict):
                    right_worth = compute_inventory_worth(inventory_b.get("items", []))

            left_queue_summary = get_player_queue_stats_summary(member_a.id)
            right_queue_summary = get_player_queue_stats_summary(member_b.id)

            image_buffer = generate_compare_image(
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

    return wrapper()


def build_leaderboard_fries_image_payload():
    users = get_all_users()
    if not users:
        return None, None, "No verified users found."

    leaderboard_data = []
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                market_data, error = await fetch_user_market(session, token)
                if error:
                    continue

                if not market_data:
                    continue

                balance = market_data.get("balance")
                if balance is None:
                    continue

                leaderboard_data.append({
                    "discord_id": discord_id,
                    "balance": balance,
                    "display_name": None
                })

        if not leaderboard_data:
            return None, None, "No Golden Fries balances could be retrieved from verified users."

        leaderboard_data.sort(key=lambda x: x["balance"], reverse=True)
        top_rows = []
        for idx, entry in enumerate(leaderboard_data[:10], start=1):
            top_rows.append({
                "rank": idx,
                "discord_id": entry["discord_id"],
                "balance": entry["balance"],
                "display_name": None
            })

        image_buffer = generate_leaderboard_fries_image(top_rows)
        file = discord.File(image_buffer, filename="leaderboard_fries.png")
        return "Server Golden Fries Leaderboard", file, None

    return wrapper()


async def generate_leaderboard_embed(period: str, days: int, sort_by: str = "kills"):
    sort_key = sort_by if sort_by in LEADERBOARD_SORT_CONFIG else "kills"
    sort_label = LEADERBOARD_SORT_CONFIG[sort_key]["label"]
    sort_emoji = LEADERBOARD_SORT_CONFIG[sort_key]["emoji"]

    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    leaderboard_data = []

    async with aiohttp.ClientSession() as session:
        for discord_id, token in users:
            stats = await fetch_player_timeframe_stats(session, token, from_ms, to_ms)
            if stats and stats["games"] > 0:
                leaderboard_data.append({
                    "discord_id": discord_id,
                    "stats": stats
                })

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

    for idx, entry in enumerate(leaderboard_data[:10]): # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        stat_value = f"{stats[sort_key]:,}" if sort_key == "damage" else str(stats[sort_key])
        leaderboard_text += f"{rank} <@{entry['discord_id']}>\n{sort_emoji} {sort_label}: **{stat_value}**\n\n"

    embed.add_field(name=f"Top Players by {sort_label}", value=leaderboard_text[:1024], inline=False)
    embed.set_footer(text="Data courtesy of survev.de API :)")
    return embed


def build_leaderboard_image_payload(period: str, days: int):
    now = datetime.now(timezone.utc)
    to_ms = int(now.timestamp() * 1000)
    from_ms = int((now - timedelta(days=days)).timestamp() * 1000)

    users = get_all_users()
    leaderboard_data = []

    async def build():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                stats = await fetch_player_timeframe_stats(session, token, from_ms, to_ms)
                if stats and stats["games"] > 0:
                    leaderboard_data.append({
                        "discord_id": discord_id,
                        "stats": stats
                    })

    async def wrapper():
        await build()
        if not leaderboard_data:
            return None, None, "No matches logged by verified members in this timeframe."

        leaderboard_data.sort(key=lambda x: x["stats"]["kills"], reverse=True)
        top_rows = []
        for idx, entry in enumerate(leaderboard_data[:10], start=1):
            top_rows.append({
                "rank": idx,
                "discord_id": entry["discord_id"],
                "stats": entry["stats"],
                "display_name": None
            })

        image_buffer = generate_leaderboard_image(period, days, top_rows)
        file = discord.File(image_buffer, filename=f"leaderboard_{period.lower()}.png")
        return f"Server {period} Leaderboard", file, None

    return wrapper()


def build_leaderboard_fries_embed():
    users = get_all_users()
    if not users:
        return None, "No verified users found."

    leaderboard_data = []
    async def wrapper():
        async with aiohttp.ClientSession() as session:
            for discord_id, token in users:
                market_data, error = await fetch_user_market(session, token)
                if error or not market_data:
                    continue

                balance = market_data.get("balance")
                if balance is None:
                    continue

                leaderboard_data.append({
                    "discord_id": discord_id,
                    "balance": balance
                })

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

    return wrapper()
