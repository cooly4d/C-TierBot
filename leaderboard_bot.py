"""Bot entrypoint: slash command registrations, Discord event handlers, and startup wiring.

All the heavy lifting (DB access, survev.de/NeatQueue API calls, image rendering, UI
components) lives in the sibling modules below — this file just wires it together.
"""
import asyncio
import re
from datetime import datetime, timezone

import aiohttp
import discord

from bot_config import (
    DISCORD_BOT_TOKEN,
    NEATQUEUE_BOT_ID,
    QUEUE_PANEL_TITLE_PATTERN,
    QUEUE_RESULT_FETCH_DELAY_SECONDS,
    QUEUE_WINNER_TITLE_PATTERN,
    bot,
)
from db import (
    clear_hall_of_fame_records,
    get_all_guild_settings,
    get_guild_queue_channel,
    get_player_queue_duration_summary,
    get_user_token,
    set_guild_queue_channel,
    try_mark_match_processed,
    update_guild_last_updated,
)
from discord_ui import (
    InventoryPaginationView,
    LeaderboardView,
    ShopPaginationView,
    get_queue_result_view,
    inventory_pagination_state,
    market_pagination_state,
    refresh_inventory_message,
    refresh_leaderboard_message,
    refresh_market_message,
)
from leaderboard_service import (
    build_compare_payload,
    build_goldenfries_embed_payload,
    build_inventory_image_payload,
    build_leaderboard_fries_embed,
    build_shop_image_payload,
    generate_leaderboard_embed,
)
from neatqueue_client import fetch_neatqueue_matches_since, get_match_game_number
from queue_stats_service import (
    build_hall_of_fame_embed,
    build_queue_stats_payload,
    format_duration_ms,
    write_queue_stats_command_log,
)
from console_service import start_console_listener
from survev_client import backfill_missing_slugs, run_survev_verification

_console_listener_started = False


@bot.event
async def on_ready():
    global _console_listener_started
    try:
        view = get_queue_result_view()
        bot.add_view(view)
        print(f"DEBUG - add_view succeeded: is_persistent={view.is_persistent()} children={view.children}")
    except Exception as exc:
        print(f"DEBUG - add_view FAILED: {exc!r}")

    try:
        # Add a persistent inventory view template (buttons will be updated per message)
        template_inventory_view = InventoryPaginationView(1)
        bot.add_view(template_inventory_view)
        print(f"DEBUG - inventory view added: is_persistent={template_inventory_view.is_persistent()}")
    except Exception as exc:
        print(f"DEBUG - inventory view add_view FAILED: {exc!r}")

    try:
        # Add a persistent shop view template (buttons will be updated per message)
        template_shop_view = ShopPaginationView(1)
        bot.add_view(template_shop_view)
        print(f"DEBUG - shop view added: is_persistent={template_shop_view.is_persistent()}")
    except Exception as exc:
        print(f"DEBUG - shop view add_view FAILED: {exc!r}")

    try:
        store = bot._connection._view_store
        print(f"DEBUG - view_store synced custom_ids: {list(store._synced_message_views.keys()) if hasattr(store, '_synced_message_views') else 'n/a'}")
        print(f"DEBUG - view_store persistent listeners: {list(getattr(store, '_views', {}).keys())}")
    except Exception as exc:
        print(f"DEBUG - view_store introspection failed: {exc!r}")

    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await backfill_missing_slugs()
    await backfill_missed_queue_results()

    if not _console_listener_started:
        _console_listener_started = True
        asyncio.create_task(start_console_listener(bot))


async def log_interaction(interaction: discord.Interaction):
    # Confirmed via logs that this fires reliably for every component click, while discord.py's own
    # View/Button dispatch mysteriously never invokes our registered callbacks — so every button in the
    # bot is handled directly here instead of relying on that broken path.
    # Registered via add_listener (not @bot.event) so it can't replace app command dispatch.
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")

    def get_selected_sort_from_message(message: discord.Message | None) -> str:
        if message is None:
            return "kills"
        for row in getattr(message, "components", []):
            for component in getattr(row, "children", []):
                if getattr(component, "custom_id", None) != "leaderboard_sort":
                    continue
                for option in getattr(component, "options", []):
                    if getattr(option, "default", False):
                        return option.value
        return "kills"

    def infer_leaderboard_period_from_message(message: discord.Message | None) -> tuple[str, int]:
        if message is None or not message.embeds:
            return "Weekly", 7
        title = message.embeds[0].title or ""
        if "Monthly" in title:
            return "Monthly", 30
        if "Weekly" in title:
            return "Weekly", 7
        return "Weekly", 7

    if custom_id == "queue_result_verify":
        print(f"DEBUG - handling queue_result_verify click for {interaction.user}")
        await interaction.response.defer(ephemeral=True)
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))
    elif custom_id == "leaderboard_weekly":
        print(f"DEBUG - handling leaderboard_weekly click for {interaction.user}")
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Weekly", 7, selected_sort)
    elif custom_id == "leaderboard_monthly":
        print(f"DEBUG - handling leaderboard_monthly click for {interaction.user}")
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Monthly", 30, selected_sort)
    elif custom_id == "leaderboard_sort":
        print(f"DEBUG - handling leaderboard_sort select for {interaction.user}")
        selected_values = interaction.data.get("values") or []
        selected_sort = selected_values[0] if selected_values else "kills"
        period, days = infer_leaderboard_period_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, period, days, selected_sort)
    elif custom_id == "inventory_prev" or custom_id == "inventory_next":
        print(f"DEBUG - handling inventory pagination {custom_id} for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in inventory_pagination_state:
            target_user, access_token, rarity_filter, current_page, total_pages = inventory_pagination_state[msg_id]
            if custom_id == "inventory_prev":
                current_page = max(0, current_page - 1)
            else:
                current_page = min(total_pages - 1, current_page + 1)
            await refresh_inventory_message(interaction, target_user, access_token, rarity_filter, current_page, msg_id)
    elif custom_id == "inventory_rarity":
        print(f"DEBUG - handling inventory rarity select for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in inventory_pagination_state:
            target_user, access_token, _, _, _ = inventory_pagination_state[msg_id]
            selected_values = interaction.data.get("values") or []
            rarity_filter = selected_values[0] if selected_values else "all"
            await refresh_inventory_message(interaction, target_user, access_token, rarity_filter, 0, msg_id)
    elif custom_id == "market_prev" or custom_id == "market_next":
        print(f"DEBUG - handling market pagination {custom_id} for {interaction.user}")
        msg_id = interaction.message.id
        if msg_id in market_pagination_state:
            target_user, access_token, mode, current_page, total_pages = market_pagination_state[msg_id]
            if custom_id == "market_prev":
                current_page = max(0, current_page - 1)
            else:
                current_page = min(total_pages - 1, current_page + 1)
            await refresh_market_message(interaction, target_user, access_token, mode, current_page, msg_id)


bot.add_listener(log_interaction, "on_interaction")


@bot.tree.command(name="verify", description="Link your survev.de account to join the server leaderboards!")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))


@bot.tree.command(name="leaderboard_weekly", description="View the top players over the past 7 days.")
async def leaderboard_weekly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed("Weekly", 7, "kills")
    await interaction.followup.send(embed=embed, view=LeaderboardView("Weekly", "kills"))


@bot.tree.command(name="leaderboard_monthly", description="View the top players over the past 30 days.")
async def leaderboard_monthly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed("Monthly", 30, "kills")
    await interaction.followup.send(embed=embed, view=LeaderboardView("Monthly", "kills"))


@bot.tree.command(name="leaderboard_fries", description="Rank users by their survev.de Golden Fries balance.")
async def leaderboard_fries(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, error = await build_leaderboard_fries_embed()
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send(embed=embed)


async def run_queue_results(interaction: discord.Interaction, match_id: str):
    await interaction.response.defer()

    if interaction.guild_id is None:
        await interaction.followup.send("❌ This command only works in a server.")
        return

    content, file, error_text, match_result = await build_queue_stats_payload(match_id, interaction.guild_id)
    write_queue_stats_command_log(interaction, match_id, match_result, error_text)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file, view=get_queue_result_view())


@bot.tree.command(name="queue_stats", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queue_stats(interaction: discord.Interaction, match_id: str):
    await run_queue_results(interaction, match_id)


@bot.tree.command(name="queueresults", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queueresults(interaction: discord.Interaction, match_id: str):
    await run_queue_results(interaction, match_id)


@bot.tree.command(name="profile", description="View a user's cached NeatQueue duration.")
@discord.app_commands.describe(member="Discord member whose NeatQueue time to display. Omit to use yourself.")
async def profile(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    total_matches, total_duration_ms = get_player_queue_duration_summary(target.id)
    if total_matches == 0:
        await interaction.followup.send(f"No cached NeatQueue results found for {target.display_name} yet.")
        return

    embed = discord.Embed(
        title=f"{target.display_name}'s NeatQueue Profile",
        color=discord.Color.blurple(),
        description="Cached queue results that matched the current-month cache rules.",
    )
    embed.add_field(name="Cached Queues", value=str(total_matches), inline=True)
    embed.add_field(name="Total Duration", value=format_duration_ms(total_duration_ms), inline=True)
    embed.set_footer(text="Only finalized queues with more than four players are cached.")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="hall_of_fame", description="View the best single-queue stats ever recorded.")
async def hall_of_fame(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await build_hall_of_fame_embed()
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="reset_hall_of_fame", description="Clear all Hall of Fame records.")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def reset_hall_of_fame(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    deleted_count = clear_hall_of_fame_records()
    await interaction.followup.send(
        f"✅ Hall of Fame reset complete. Removed {deleted_count} record(s).",
        ephemeral=True,
    )


@bot.tree.command(name="inventory", description="View a user's survev.de inventory")
@discord.app_commands.describe(member="Discord member whose inventory to display. Omit to use yourself.")
async def inventory(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = get_user_token(target.id)
    if not access_token:
        if target.id == interaction.user.id:
            await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` first.")
        else:
            await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
        return

    content, file, error_text, total_pages, filename, rarity_counts = await build_inventory_image_payload(target, access_token, 0, "all")
    if error_text:
        await interaction.followup.send(error_text)
        return

    # Create embed with image attached
    embed = discord.Embed(title=f"{content} (All Rarities)", color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page 1 of {total_pages or 1}")
    
    view = InventoryPaginationView(total_pages or 1, "all", rarity_counts)
    view.update_button_states(0)
    msg = await interaction.followup.send(embed=embed, file=file, view=view)
    
    # Store pagination state
    inventory_pagination_state[msg.id] = (target, access_token, "all", 0, total_pages or 1)


@bot.tree.command(name="goldenfries", description="View a user's survev.de Golden Fries balance.")
@discord.app_commands.describe(member="Discord member whose balance to display. Omit to use yourself.")
async def goldenfries(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = get_user_token(target.id)
    if not access_token:
        if target.id == interaction.user.id:
            await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
        else:
            await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
        return

    username, balance, error_text = await build_goldenfries_embed_payload(target, access_token)
    if error_text:
        await interaction.followup.send(error_text)
        return

    embed = discord.Embed(
        title="<:goldenfries:1535978920481136700> Golden Fries Balance",
        description=f"**{username}** currently has **{balance:,}** Golden Fries <:goldenfries:1535978920481136700>.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Data courtesy of survev.de API :)")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="compare", description="Compare two members' survev.de stats side-by-side.")
@discord.app_commands.describe(
    member_a="First Discord member to compare",
    member_b="Second Discord member to compare. Omit to compare yourself."
)
async def compare(
    interaction: discord.Interaction,
    member_a: discord.User,
    member_b: discord.User | None = None,
):
    member_b = member_b or interaction.user
    await interaction.response.defer()

    content, file, error_text = await build_compare_payload(member_a, member_b)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file)


class MarketGroup(discord.app_commands.Group):
    def __init__(self):
        super().__init__(name="market", description="View a user's survev.de shop offers.")

    @discord.app_commands.command(name="daily", description="View only daily shop offers.")
    @discord.app_commands.describe(member="Discord member whose shop to display. Omit to use yourself.")
    async def daily(self, interaction: discord.Interaction, member: discord.User | None = None):
        target = member or interaction.user
        await interaction.response.defer()

        access_token = get_user_token(target.id)
        if not access_token:
            if target.id == interaction.user.id:
                await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
            else:
                await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
            return

        content, file, filename, error_text, total_pages = await build_shop_image_payload(target, access_token, mode="daily", page=0)
        if error_text:
            await interaction.followup.send(error_text)
            return

        # Create embed with image
        embed = discord.Embed(title=f"{content}", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page 1 of {total_pages}")

        # Create pagination view
        view = ShopPaginationView(total_pages)
        view.update_button_states(0)

        msg = await interaction.followup.send(embed=embed, file=file, view=view)
        
        # Store pagination state
        market_pagination_state[msg.id] = (target, access_token, "daily", 0, total_pages)

    @discord.app_commands.command(name="weekly", description="View only weekly shop offers.")
    @discord.app_commands.describe(member="Discord member whose shop to display. Omit to use yourself.")
    async def weekly(self, interaction: discord.Interaction, member: discord.User | None = None):
        target = member or interaction.user
        await interaction.response.defer()

        access_token = get_user_token(target.id)
        if not access_token:
            if target.id == interaction.user.id:
                await interaction.followup.send("You have not linked a survev.de account yet. Use `/verify` to get started.")
            else:
                await interaction.followup.send(f"{target.mention} has not linked a survev.de account yet. Use `/verify` to get started.")
            return

        content, file, filename, error_text, total_pages = await build_shop_image_payload(target, access_token, mode="weekly", page=0)
        if error_text:
            await interaction.followup.send(error_text)
            return

        # Create embed with image
        embed = discord.Embed(title=f"{content}", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page 1 of {total_pages}")

        # Create pagination view
        view = ShopPaginationView(total_pages)
        view.update_button_states(0)

        msg = await interaction.followup.send(embed=embed, file=file, view=view)
        
        # Store pagination state
        market_pagination_state[msg.id] = (target, access_token, "weekly", 0, total_pages)


bot.tree.add_command(MarketGroup())


# ------------------------------------------------------------------
# Automatic NeatQueue result detection
# ------------------------------------------------------------------
@bot.tree.command(name="setup", description="Set the channel NeatQueue posts match results in, so stats get posted automatically.")
@discord.app_commands.describe(channel="The channel where the NeatQueue bot posts its match result embeds")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    set_guild_queue_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(
        f"✅ NeatQueue results in {channel.mention} will now be tracked automatically.",
        ephemeral=True
    )


def find_neatqueue_embed_match(message: discord.Message, title_patterns: tuple[re.Pattern, ...]):
    """Returns the match_id if a NeatQueue embed in this message's configured channel matches any of title_patterns."""
    if message.author.id != NEATQUEUE_BOT_ID:
        return None

    if message.guild is None or not message.embeds:
        return None

    configured_channel_id = get_guild_queue_channel(message.guild.id)
    if configured_channel_id is None or message.channel.id != configured_channel_id:
        return None

    for embed in message.embeds:
        if not embed.title:
            continue
        for title_pattern in title_patterns:
            match = title_pattern.search(embed.title)
            if match:
                return match.group(1)

    return None


async def post_queue_result(message: discord.Message, match_id: str):
    """Marks match_id as processed for this guild and, if new, replies to message with its stats."""
    if not try_mark_match_processed(message.guild.id, match_id):
        return  # already posted (e.g. the panel got edited again after a revert)

    # Give the game server a moment to finish writing this match's results before querying survev.de.
    await asyncio.sleep(QUEUE_RESULT_FETCH_DELAY_SECONDS)

    content, file, error_text, _ = await build_queue_stats_payload(match_id, message.guild.id)
    if error_text:
        await message.reply(error_text)
    else:
        await message.reply(content=content, file=file, view=get_queue_result_view())

    # Mark this guild caught up so a later restart doesn't re-post this match during backfill.
    update_guild_last_updated(message.guild.id, datetime.now(timezone.utc).isoformat())


@bot.event
async def on_message(message: discord.Message):
    if message.guild is not None and message.author.id != bot.user.id:
        # The winner announcement is sometimes already complete when first posted...
        match_id = find_neatqueue_embed_match(message, (QUEUE_WINNER_TITLE_PATTERN,))
        if match_id is not None:
            await post_queue_result(message, match_id)
    await bot.process_commands(message)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    # ...but NeatQueue also edits messages in place to fill in/finalize a result (the admin "Results for
    # Queue#" panel always works this way, and the "Winner For Queue#" announcement sometimes does too).
    # Uses the raw event (not on_message_edit) because on_message_edit only fires for messages still in
    # discord.py's message cache, and these messages are often edited long after they scroll out of cache.
    if payload.guild_id is None:
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.HTTPException:
        return

    if message.author.id == bot.user.id:
        return

    match_id = find_neatqueue_embed_match(message, (QUEUE_WINNER_TITLE_PATTERN, QUEUE_PANEL_TITLE_PATTERN))
    if match_id is not None:
        await post_queue_result(message, match_id)


async def backfill_missed_queue_results():
    """On startup, catch up on any NeatQueue matches that finished while the bot was offline."""
    guild_configs = get_all_guild_settings()
    if not guild_configs:
        return

    async with aiohttp.ClientSession() as session:
        for guild_id, channel_id, last_updated in guild_configs:
            matches, error = await fetch_neatqueue_matches_since(session, guild_id, last_updated)
            if error:
                continue
            if not matches:
                continue

            channel = bot.get_channel(channel_id)
            if channel is None:
                continue

            for match in matches:
                match_id = get_match_game_number(match)
                if match_id is None:
                    continue
                match_id = str(match_id)

                if not try_mark_match_processed(guild_id, match_id):
                    continue  # already posted live before the bot went down

                content, file, error_text, _ = await build_queue_stats_payload(match_id, guild_id)
                if error_text:
                    continue  # nothing worth posting (e.g. no verified players), skip silently on catch-up
                await channel.send(content=f"*(Catching up)* {content}", file=file, view=get_queue_result_view())

            update_guild_last_updated(guild_id, datetime.now(timezone.utc).isoformat())


# --- RUN BOT ---
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
