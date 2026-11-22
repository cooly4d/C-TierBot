"""Bot entrypoint: slash command registrations, Discord event handlers, and startup wiring.

All the heavy lifting (DB access, survev.de/NeatQueue API calls, image rendering, UI
components) lives in the sibling modules below — this file just wires it together.
"""
import asyncio
import logging
import re
import signal
import time
from datetime import datetime, timezone

import discord
from discord.ext import tasks

from bot_config import (
    CONSOLE_PORT,
    DISCORD_BOT_TOKEN,
    KEYWORD_REACTIONS,
    LOG_LEVEL,
    NEATQUEUE_BOT_ID,
    QUEUE_PANEL_TITLE_PATTERN,
    QUEUE_RESULT_FETCH_DELAY_SECONDS,
    QUEUE_RESULT_MAX_MESSAGE_AGE_SECONDS,
    QUEUE_WINNER_TITLE_PATTERN,
    bot,
)
from db import (
    add_guild_queue_channel,
    clear_hall_of_fame_records,
    count_hall_of_fame_records,
    get_all_guild_settings,
    get_guild_queue_channels,
    get_user_token,
    remove_guild_queue_channel,
    try_mark_match_processed,
    update_guild_last_updated,
)
from discord_ui import (
    HOF_RESET_CONFIRM_SECONDS,
    LEGACY_PAGINATION_IDS,
    HallOfFameResetView,
    LeaderboardView,
    build_inventory_message,
    build_market_message,
    build_profile_message,
    build_seasonal_leaderboard_message,
    get_queue_result_view,
    parse_game_page_id,
    parse_history_metric_id,
    parse_hof_reset_id,
    parse_inventory_page_id,
    parse_market_id,
    parse_profile_tab_id,
    parse_season_page_id,
    refresh_inventory_message,
    refresh_leaderboard_message,
    refresh_market_message,
    refresh_profile_message,
    refresh_queue_game_message,
    refresh_seasonal_leaderboard_message,
    send_expired_menu_notice,
    show_queue_game_breakdown,
)
from http_client import close_http_session, http_session
from leaderboard_service import (
    build_compare_payload,
    build_goldenfries_embed_payload,
    build_leaderboard_fries_embed,
    generate_leaderboard_embed,
    link_prompt,
    survev_error_for,
)
from neatqueue_client import fetch_neatqueue_matches_since, get_match_game_number, get_nested_value
from queue_stats_service import (
    build_hall_of_fame_embed,
    build_queue_stats_payload,
    save_queue_stats_log,
)
from console_service import start_console_listener, start_console_socket_server
from survev_client import backfill_missing_slugs, run_survev_verification

log = logging.getLogger(__name__)
NO_PINGS = discord.AllowedMentions.none()  # replies that name people or quote what someone typed

_startup_done = False
_console_listener_started = False
_console_socket_server: asyncio.AbstractServer | None = None
SLUG_BACKFILL_INTERVAL_MINUTES = 30
QUEUE_MENTION_COOLDOWN_SECONDS = 30
_queue_mention_busy: set[tuple[int, int]] = set()
_queue_mention_cooldowns: dict[tuple[int, int], float] = {}
_prefix_queue_busy: set[tuple[int, int, str]] = set()
_bot_owner_id: int | None = None  # resolved once from the application info
_hof_reset_answered: set[tuple[int, int]] = set()  # (user_id, expires_at) of answered reset confirmations
# "score?" anywhere in a message, as its own word (so "what is the score?" counts but "underscore?" doesn't).
SCORE_QUESTION_PATTERN = re.compile(r"\bscore\?", re.IGNORECASE)
# "?c <arg>" or "?c<queue_number>" in any server channel the bot can post in (e.g. "?c4123", "?c 4123", "?C #4123").
# A word argument needs the space, so "?cool" is never a command.
PREFIX_COMMAND_PATTERN = re.compile(r"^\s*\?c(?:\s+(.+?)|(#?[0-9]{1,7}))\s*$", re.IGNORECASE | re.DOTALL)
PREFIX_QUEUE_NUMBER_PATTERN = re.compile(r"^#?([0-9]{1,7})$")


@tasks.loop(minutes=SLUG_BACKFILL_INTERVAL_MINUTES)
async def slug_backfill_loop():
    """Periodically fills in survev.de slugs for linked players whose lookup failed at /verify time, so they
    stop showing up as guests on match cards without waiting for the next bot restart."""
    if slug_backfill_loop.current_loop == 0:
        return  # on_ready already ran a backfill just now
    try:
        await backfill_missing_slugs()
    except Exception as exc:
        # An unhandled error would stop the loop for good; log and try again next interval instead.
        log.warning("Slug backfill failed: %r", exc)


@bot.event
async def on_ready():
    global _startup_done, _console_listener_started, _console_socket_server
    # on_ready fires again after every reconnect; registering views and syncing commands once is enough.
    if not _startup_done:
        try:
            bot.add_view(get_queue_result_view(include_breakdown=True))
        except Exception as exc:
            log.warning("add_view failed for QueueResultView: %r", exc)
        await bot.tree.sync()
        _startup_done = True  # only after a successful sync, so a failed one is retried on the next reconnect
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
    await backfill_missing_slugs()
    if not slug_backfill_loop.is_running():
        slug_backfill_loop.start()
    await backfill_missed_queue_results()

    if not _console_listener_started:
        _console_listener_started = True
        asyncio.create_task(start_console_listener(bot))
        if CONSOLE_PORT > 0:
            _console_socket_server = await start_console_socket_server(bot, CONSOLE_PORT)


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

    log.debug("Component %s clicked by %s", custom_id, interaction.user)
    if custom_id == "queue_result_verify":
        await interaction.response.defer(ephemeral=True)
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))
    elif custom_id == "queue_result_games":
        await show_queue_game_breakdown(interaction)
    elif custom_id and custom_id.startswith(("qgame_prev:", "qgame_next:")):
        match_id, page = parse_game_page_id(custom_id)
        await refresh_queue_game_message(interaction, match_id, page)
    elif custom_id and custom_id.startswith("qgame_pick:"):
        picked = (interaction.data.get("values") or ["0"])[0]
        await refresh_queue_game_message(interaction, custom_id.split(":", 1)[1], int(picked) if picked.isdigit() else 0)
    elif custom_id == "leaderboard_weekly":
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Weekly", 7, selected_sort)
    elif custom_id == "leaderboard_monthly":
        selected_sort = get_selected_sort_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, "Monthly", 30, selected_sort)
    elif custom_id == "leaderboard_sort":
        selected_values = interaction.data.get("values") or []
        selected_sort = selected_values[0] if selected_values else "kills"
        period, days = infer_leaderboard_period_from_message(interaction.message)
        await refresh_leaderboard_message(interaction, period, days, selected_sort)
    elif custom_id and custom_id.startswith(("season_prev:", "season_next:")):
        month, stat, page = parse_season_page_id(custom_id)
        await refresh_seasonal_leaderboard_message(interaction, month, page, stat)
    elif custom_id and custom_id.startswith("season_stat:"):
        selected_values = interaction.data.get("values") or []
        stat = selected_values[0] if selected_values else "damage"
        await refresh_seasonal_leaderboard_message(interaction, custom_id.split(":", 1)[1], 0, stat)
    elif custom_id and custom_id.startswith(("inv_prev:", "inv_next:")):
        target_id, rarity_filter, page = parse_inventory_page_id(custom_id)
        await refresh_inventory_message(interaction, target_id, rarity_filter, page)
    elif custom_id and custom_id.startswith("inv_rarity:"):
        selected_values = interaction.data.get("values") or []
        rarity_filter = selected_values[0] if selected_values else "all"
        await refresh_inventory_message(interaction, int(custom_id.split(":", 1)[1]), rarity_filter, 0)
    elif custom_id and custom_id.startswith(("mkt_prev:", "mkt_next:", "mkt_mode:")):
        target_id, mode, page = parse_market_id(custom_id)
        await refresh_market_message(interaction, target_id, mode, page)
    elif custom_id and custom_id.startswith("hof_reset:"):
        await answer_hall_of_fame_reset(interaction, custom_id)
    elif custom_id and custom_id.startswith("profile_tab:"):
        tab, guild_id, target_discord_id = parse_profile_tab_id(custom_id)
        await refresh_profile_message(interaction, guild_id, target_discord_id, tab)
    elif custom_id and custom_id.startswith("history_metric:"):
        # /profile's trend buttons (the id format comes from the old /history command, whose messages still work).
        metric, guild_id, target_discord_id = parse_history_metric_id(custom_id)
        await refresh_profile_message(interaction, guild_id, target_discord_id, "history", metric)
    elif custom_id in LEGACY_PAGINATION_IDS:
        await send_expired_menu_notice(interaction, LEGACY_PAGINATION_IDS[custom_id])


bot.add_listener(log_interaction, "on_interaction")


async def _require_user_token(interaction: discord.Interaction, target: discord.User) -> str | None:
    """Returns target's survev.de access token, or sends an unlinked-account message and returns None."""
    access_token = get_user_token(target.id)
    if access_token:
        return access_token
    await interaction.followup.send(link_prompt(target, interaction.user.id), allowed_mentions=NO_PINGS)
    return None


@bot.tree.command(name="verify", description="Link your survev.de account to join the server leaderboards!")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))


@bot.tree.command(name="leaderboard_old", description="(Legacy) View the top players over the past 7 or 30 days.")
async def leaderboard_old(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed("Weekly", 7, "kills")
    await interaction.followup.send(embed=embed, view=LeaderboardView("Weekly", "kills"))


@bot.tree.command(name="leaderboard_season", description="View the cached 4v4 average-damage leaderboard for a calendar month.")
@discord.app_commands.describe(month="UTC calendar month in YYYY-MM format. Omit for the current month.")
@discord.app_commands.guild_only()
async def leaderboard_season(interaction: discord.Interaction, month: str | None = None):
    await interaction.response.defer()
    try:
        embed, view = await build_seasonal_leaderboard_message(interaction.guild_id, month)
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="leaderboard_fries", description="Rank users by their survev.de Golden Fries balance.")
async def leaderboard_fries(interaction: discord.Interaction):
    await interaction.response.defer()
    embed, error = await build_leaderboard_fries_embed()
    if error:
        await interaction.followup.send(error)
        return
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="queue_stats", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queue_stats(interaction: discord.Interaction, match_id: str):
    await interaction.response.defer()

    if interaction.guild_id is None:
        await interaction.followup.send("❌ This command only works in a server.")
        return

    content, file, error_text, match_result = await build_queue_stats_payload(match_id, interaction.guild_id)
    await save_queue_stats_log(
        match_id, match_result, error_text, trigger="queue_stats_command",
        guild_id=interaction.guild_id, channel_id=interaction.channel_id, requested_by=interaction.user,
    )
    if error_text:
        await interaction.followup.send(error_text, allowed_mentions=NO_PINGS)  # it can quote the queue number typed
        return

    await interaction.followup.send(
        content=content, file=file, view=get_queue_result_view(include_breakdown=bool(match_result.get("is_final"))),
    )


@bot.tree.command(name="profile", description="View a player's NeatQueue stats, recent queues, and survev.de Squad tier.")
@discord.app_commands.describe(member="Discord member whose stats to display. Omit to use yourself.")
async def profile(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    embed, _file, view, message = await build_profile_message(target, interaction.guild_id,
                                                              viewer_id=interaction.user.id)
    if message:
        await interaction.followup.send(message, allowed_mentions=NO_PINGS)
        return
    if view is None:  # nothing to switch to (no history in this server, or used outside one); send rejects view=None
        await interaction.followup.send(embed=embed)
        return
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="hall_of_fame", description="View the best single-queue stats ever recorded.")
async def hall_of_fame(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await build_hall_of_fame_embed()
    await interaction.followup.send(embed=embed)


async def _is_bot_owner(user: discord.abc.User) -> bool:
    """True only for the application's owner: the individual owner, or the owner of the team that owns it. Unlike
    bot.is_owner, other team members don't count."""
    global _bot_owner_id
    if _bot_owner_id is None:
        app = await bot.application_info()
        _bot_owner_id = app.team.owner_id if app.team else app.owner.id
    return user.id == _bot_owner_id


@bot.tree.command(name="reset_hall_of_fame",
                  description="Delete every Hall of Fame record, in every server (bot owner only).")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def reset_hall_of_fame(interaction: discord.Interaction):
    """Hall of Fame records are global, so only the bot's owner may delete them, and only after confirming."""
    if not await _is_bot_owner(interaction.user):
        await interaction.response.send_message("Only the bot's owner can reset the Hall of Fame.", ephemeral=True)
        return
    record_count = count_hall_of_fame_records()
    if record_count == 0:
        await interaction.response.send_message("The Hall of Fame has no records to delete.", ephemeral=True)
        return
    plural = "record" if record_count == 1 else "records"
    await interaction.response.send_message(
        f"This deletes all {record_count} Hall of Fame {plural}, for every server that uses C-TierBot. "
        "It can't be undone.",
        view=HallOfFameResetView(interaction.user.id, int(time.time()) + HOF_RESET_CONFIRM_SECONDS, record_count),
        ephemeral=True,
    )


async def answer_hall_of_fame_reset(interaction: discord.Interaction, custom_id: str):
    """The Delete / Cancel buttons under /reset_hall_of_fame's confirmation."""
    action, user_id, expires_at = parse_hof_reset_id(custom_id)
    if interaction.user.id != user_id or not await _is_bot_owner(interaction.user):
        await interaction.response.send_message("Only the bot's owner can reset the Hall of Fame.", ephemeral=True)
        return
    now = time.time()
    for spent in [key for key in _hof_reset_answered if key[1] < now]:
        _hof_reset_answered.discard(spent)
    if (user_id, expires_at) in _hof_reset_answered:  # a second click that arrived before the buttons were removed
        await interaction.response.send_message("This confirmation was already answered.", ephemeral=True)
        return
    _hof_reset_answered.add((user_id, expires_at))
    if action != "confirm":
        await interaction.response.edit_message(content="Nothing was deleted.", view=None)
        return
    if time.time() > expires_at:
        await interaction.response.edit_message(
            content="This confirmation ran out, so nothing was deleted. Run /reset_hall_of_fame again.", view=None)
        return
    deleted_count = clear_hall_of_fame_records()
    await interaction.response.edit_message(
        content=f"✅ Deleted {deleted_count} Hall of Fame record{'' if deleted_count == 1 else 's'}.", view=None)


@bot.tree.command(name="inventory", description="View a user's survev.de inventory")
@discord.app_commands.describe(member="Discord member whose inventory to display. Omit to use yourself.")
async def inventory(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = await _require_user_token(interaction, target)
    if not access_token:
        return

    embed, file, view, error_text = await build_inventory_message(target, access_token)
    if error_text:
        await interaction.followup.send(survev_error_for(error_text, target, interaction.user.id), allowed_mentions=NO_PINGS)
        return
    await interaction.followup.send(embed=embed, file=file, view=view)


@bot.tree.command(name="goldenfries", description="View a user's survev.de Golden Fries balance.")
@discord.app_commands.describe(member="Discord member whose balance to display. Omit to use yourself.")
async def goldenfries(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = await _require_user_token(interaction, target)
    if not access_token:
        return

    username, balance, error_text = await build_goldenfries_embed_payload(target, access_token)
    if error_text:
        await interaction.followup.send(survev_error_for(error_text, target, interaction.user.id), allowed_mentions=NO_PINGS)
        return

    embed = discord.Embed(
        title="<:goldenfries:1535978920481136700> Golden Fries balance",
        description=f"**{username}** currently has **{balance:,}** Golden Fries <:goldenfries:1535978920481136700>.",
        color=discord.Color.gold(),
    )
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
    # The person asking always reads their own column on the left.
    if member_b.id == interaction.user.id and member_a.id != interaction.user.id:
        member_a, member_b = member_b, member_a
    await interaction.response.defer()

    content, file, error_text = await build_compare_payload(member_a, member_b)
    if error_text:
        await interaction.followup.send(error_text)
        return

    await interaction.followup.send(content=content, file=file)


@bot.tree.command(name="market", description="View a user's survev.de shop offers.")
@discord.app_commands.describe(member="Discord member whose shop to display. Omit to use yourself.")
async def market(interaction: discord.Interaction, member: discord.User | None = None):
    target = member or interaction.user
    await interaction.response.defer()

    access_token = await _require_user_token(interaction, target)
    if not access_token:
        return

    embed, file, view, error_text = await build_market_message(target, access_token, mode="all")
    if error_text:
        await interaction.followup.send(survev_error_for(error_text, target, interaction.user.id), allowed_mentions=NO_PINGS)
        return
    await interaction.followup.send(embed=embed, file=file, view=view)


# ------------------------------------------------------------------
# Automatic NeatQueue result detection
# ------------------------------------------------------------------
@bot.tree.command(name="setup", description="Add a channel NeatQueue posts match results in, so stats get posted automatically.")
@discord.app_commands.describe(channel="A channel where the NeatQueue bot posts its match result embeds")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    added = add_guild_queue_channel(interaction.guild_id, channel.id)
    message = (
        f"✅ NeatQueue results in {channel.mention} will now be tracked automatically."
        if added else f"{channel.mention} is already being tracked."
    )
    tracked = ", ".join(f"<#{cid}>" for cid in get_guild_queue_channels(interaction.guild_id))
    await interaction.response.send_message(f"{message}\nTracked channels: {tracked}", ephemeral=True)


@bot.tree.command(name="unsetup", description="Stop tracking NeatQueue results in a channel.")
@discord.app_commands.describe(channel="The channel to stop tracking")
@discord.app_commands.default_permissions(administrator=True)
@discord.app_commands.guild_only()
async def unsetup(interaction: discord.Interaction, channel: discord.TextChannel):
    removed = remove_guild_queue_channel(interaction.guild_id, channel.id)
    message = (
        f"✅ {channel.mention} is no longer tracked." if removed else f"{channel.mention} wasn't being tracked."
    )
    await interaction.response.send_message(message, ephemeral=True)


def find_neatqueue_embed_match(message: discord.Message, title_patterns: tuple[re.Pattern, ...]):
    """Returns the match_id if a NeatQueue embed in this message's configured channel matches any of title_patterns."""
    if message.author.id != NEATQUEUE_BOT_ID:
        return None

    if message.guild is None or not message.embeds:
        return None

    if message.channel.id not in get_guild_queue_channels(message.guild.id):
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
    message_age_seconds = (datetime.now(timezone.utc) - message.created_at).total_seconds()
    if message_age_seconds > QUEUE_RESULT_MAX_MESSAGE_AGE_SECONDS:
        log.info(
            "Ignoring Queue#%s: message %s is %.1fh old (created %s, last edited %s)",
            match_id, message.id, message_age_seconds / 3600, message.created_at.isoformat(),
            message.edited_at.isoformat() if message.edited_at else "never",
        )
        return  # old message touched by a non-edit update or re-sent, not a freshly finished queue

    if not try_mark_match_processed(message.guild.id, match_id):
        return  # already posted (e.g. the panel got edited again after a revert)

    # Give the game server a moment to finish writing this match's results before querying survev.de.
    await asyncio.sleep(QUEUE_RESULT_FETCH_DELAY_SECONDS)

    content, file, error_text, match_result = await build_queue_stats_payload(match_id, message.guild.id)
    await save_queue_stats_log(
        match_id, match_result, error_text, trigger="auto_post", guild_id=message.guild.id, channel_id=message.channel.id,
    )
    if error_text:
        await message.reply(error_text)
    else:
        view = get_queue_result_view(include_breakdown=bool(match_result.get("is_final")))
        await message.reply(content=content, file=file, view=view)

    # Mark this guild caught up so a later restart doesn't re-post this match during backfill.
    update_guild_last_updated(message.guild.id, datetime.now(timezone.utc).isoformat())


async def _send_queue_stats_reply(message: discord.Message, match_id: str, *, trigger: str) -> bool:
    """Fetches and replies with queue stats for match_id. Returns True when stats or a lookup error were sent."""
    reply_options = {"allowed_mentions": discord.AllowedMentions.none(), "mention_author": False}
    file = None
    try:
        async with message.channel.typing():
            content, file, error_text, match_result = await build_queue_stats_payload(match_id, message.guild.id)
            await save_queue_stats_log(
                match_id, match_result, error_text, trigger=trigger,
                guild_id=message.guild.id, channel_id=message.channel.id, requested_by=message.author,
            )
            if error_text:
                await message.reply(error_text, **reply_options)
            else:
                await message.reply(
                    content=content, file=file,
                    view=get_queue_result_view(include_breakdown=bool(match_result.get("is_final"))),
                    **reply_options,
                )
        return True
    except discord.HTTPException as exc:
        # Missing send permissions or a deleted request/channel must not break the listener.
        log.warning("Could not reply in the Queue#%s channel: %s", match_id, type(exc).__name__)
        return False
    except Exception as exc:
        log.warning("Queue#%s lookup for %s failed: %s", match_id, trigger, type(exc).__name__)
        try:
            await message.reply("I couldn't fetch this queue's stats. Please try again shortly.", **reply_options)
        except discord.HTTPException as reply_exc:
            log.warning("Could not reply in the Queue#%s channel: %s", match_id, type(reply_exc).__name__)
        return False
    finally:
        if file is not None:
            file.close()


async def reply_to_queue_mention(message: discord.Message) -> bool:
    """Reply to human mentions or 'score?' in queue channels without consuming automatic result tracking."""
    if message.guild is None or message.author.bot or message.webhook_id is not None or bot.user is None:
        return False
    match = re.fullmatch(r"queue-([0-9]+)", getattr(message.channel, "name", ""))
    if match is None:
        return False
    if SCORE_QUESTION_PATTERN.search(message.content) is None:
        # Otherwise require an explicit user mention, not @everyone, a role, or a reply's implicit ping.
        if not any(user.id == bot.user.id for user in message.mentions):
            return False
        if re.search(rf"<@!?{bot.user.id}>", message.content) is None:
            return False

    match_id = match.group(1)
    key = (message.guild.id, message.channel.id)
    now = time.monotonic()
    for expired_key, deadline in list(_queue_mention_cooldowns.items()):
        if deadline <= now:
            del _queue_mention_cooldowns[expired_key]

    # Silently consume repeated requests so the cooldown cannot itself spam the channel.
    if key in _queue_mention_busy:
        return True
    remaining = _queue_mention_cooldowns.get(key, now) - now
    if remaining > 0:
        return True

    # Claim before the first await so simultaneous pings cannot start duplicate lookups.
    _queue_mention_busy.add(key)
    try:
        if await _send_queue_stats_reply(message, match_id, trigger="queue_channel_reply"):
            _queue_mention_cooldowns[key] = time.monotonic() + QUEUE_MENTION_COOLDOWN_SECONDS
    finally:
        _queue_mention_busy.discard(key)
    return True


def _can_post_queue_card(message: discord.Message) -> bool:
    """Whether the bot may answer this message with a stats card: send messages (in a thread, send in threads), attach
    the image, and read message history, which Discord requires for replies. Being able to read a channel isn't enough."""
    channel = message.channel
    try:
        permissions = channel.permissions_for(message.guild.me)
    except discord.ClientException:  # a thread whose parent channel isn't cached
        return False
    can_send = permissions.send_messages_in_threads if isinstance(channel, discord.Thread) else permissions.send_messages
    return can_send and permissions.attach_files and permissions.read_message_history


async def reply_to_prefix_command(message: discord.Message) -> bool:
    """Handle '?c <command>' shortcuts in any server channel the bot can post in (currently '?c <queue_number>').
    A matching message in a channel the bot can't answer in is consumed silently, so no lookup is wasted on it."""
    if message.guild is None or message.author.bot or message.webhook_id is not None:
        return False
    prefix_match = PREFIX_COMMAND_PATTERN.match(message.content or "")
    if prefix_match is None:
        return False
    if not _can_post_queue_card(message):
        return True
    arg = (prefix_match.group(1) or prefix_match.group(2)).strip()

    queue_match = PREFIX_QUEUE_NUMBER_PATTERN.fullmatch(arg)
    if queue_match is not None:
        match_id = queue_match.group(1)
        busy_key = (message.guild.id, message.channel.id, match_id)
        if busy_key in _prefix_queue_busy:
            return True
        _prefix_queue_busy.add(busy_key)
        try:
            await _send_queue_stats_reply(message, match_id, trigger="queue_prefix_command")
        finally:
            _prefix_queue_busy.discard(busy_key)
        return True

    return False


async def react_to_keywords(message: discord.Message) -> None:
    """React with each KEYWORD_REACTIONS emoji whose word appears anywhere in a message, in any channel."""
    content = (message.content or "").casefold()
    if not content or message.author.bot:
        return
    for keyword, emoji in KEYWORD_REACTIONS.items():
        if keyword not in content:
            continue
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException as error:
            # Missing Add Reactions / Read Message History, a deleted emoji, or the author blocked the bot.
            log.debug("Keyword reaction %r skipped: %s (status %s)", keyword, type(error).__name__, error.status)


@bot.event
async def on_message(message: discord.Message):
    await react_to_keywords(message)
    if await reply_to_queue_mention(message):
        return
    if await reply_to_prefix_command(message):
        return
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
    # Only tracked results channels can hold a NeatQueue result (find_neatqueue_embed_match checks this too). Checking
    # first saves fetching every message edited anywhere else in the server from Discord.
    if payload.channel_id not in get_guild_queue_channels(payload.guild_id):
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

    channels_by_guild: dict[int, list[int]] = {}
    oldest_update: dict[int, str] = {}
    for guild_id, channel_id, last_updated in guild_configs:
        channels_by_guild.setdefault(guild_id, []).append(channel_id)
        oldest_update[guild_id] = min(oldest_update.get(guild_id, last_updated), last_updated)

    for guild_id, channel_ids in channels_by_guild.items():
        matches, error = await fetch_neatqueue_matches_since(http_session, guild_id, oldest_update[guild_id])
        if error:
            continue
        if not matches:
            continue

        for match in matches:
            match_id = get_match_game_number(match)
            if match_id is None:
                continue
            match_id = str(match_id)

            # Post where NeatQueue announced the winner; with a single tracked channel, fall back to it.
            winner_channel_id = get_nested_value(match, "winner_channel")
            try:
                target_channel_id = int(winner_channel_id) if winner_channel_id else None
            except (TypeError, ValueError):
                target_channel_id = None
            if target_channel_id not in channel_ids:
                if len(channel_ids) != 1:
                    continue
                target_channel_id = channel_ids[0]

            channel = bot.get_channel(target_channel_id)
            if channel is None:
                continue

            if not try_mark_match_processed(guild_id, match_id):
                continue  # already posted live before the bot went down

            content, file, error_text, match_result = await build_queue_stats_payload(match_id, guild_id)
            await save_queue_stats_log(
                match_id, match_result, error_text, trigger="catch_up", guild_id=guild_id, channel_id=target_channel_id,
            )
            if error_text:
                continue  # nothing worth posting (e.g. no verified players), skip silently on catch-up
            view = get_queue_result_view(include_breakdown=bool(match_result.get("is_final")))
            await channel.send(content=f"*(Catching up)* {content}", file=file, view=view)

        update_guild_last_updated(guild_id, datetime.now(timezone.utc).isoformat())


def install_shutdown_signal_handler(loop: asyncio.AbstractEventLoop) -> bool:
    """Close the bot cleanly on SIGTERM, which Docker/Coolify sends to stop or redeploy the container.
    As the container's PID 1, Python would otherwise ignore it and be killed once the grace period ends.
    Windows has no loop signal handlers, so this is a no-op there."""
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.ensure_future(bot.close()))
    except (NotImplementedError, AttributeError, RuntimeError):
        return False
    return True


async def main():
    install_shutdown_signal_handler(asyncio.get_running_loop())
    async with bot:
        try:
            await bot.start(DISCORD_BOT_TOKEN)
        finally:
            await close_http_session()


# --- RUN BOT ---
if __name__ == "__main__":
    # The same console log format discord.py's bot.run() sets up, applied to the bot's own module loggers too.
    discord.utils.setup_logging(level=LOG_LEVEL, root=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
