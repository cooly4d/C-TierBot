"""Discord UI components: persistent Views/Selects for the leaderboards, queue-result
verify and Game breakdown buttons, /profile's trend buttons, and inventory/shop paging, plus the
message-refresh handlers they trigger.

Note: the actual click/select dispatch lives in `log_interaction` (leaderboard_bot.py) —
these views' own callbacks are no-ops. Paged views carry their state (whose data, which page or filter) in
each component's custom_id, so their buttons keep working after a restart.
"""
import discord

from bot_config import LEADERBOARD_SORT_CONFIG
from db import get_user_token
from image_utils import (
    INVENTORY_RARITY_FILTERS,
    normalize_history_metric,
    normalize_inventory_rarity_filter,
)
from leaderboard_service import (
    SEASON_STATS,
    build_inventory_image_payload,
    build_profile_history,
    build_profile_overview,
    build_seasonal_leaderboard_payload,
    build_shop_image_payload,
    generate_leaderboard_embed,
    has_cached_inventory,
    has_cached_market,
    normalize_season_stat,
)
from queue_stats_service import build_queue_game_payload, parse_queue_match_id, queue_games_unavailable

MARKET_MODES = ("daily", "weekly", "all")
# custom_ids used by /inventory and /market messages sent before paging state moved into the custom_id.
LEGACY_PAGINATION_IDS = {
    "inventory_prev": "/inventory", "inventory_next": "/inventory", "inventory_rarity": "/inventory",
    "market_prev": "/market", "market_next": "/market",
    "market_mode_daily": "/market", "market_mode_weekly": "/market", "market_mode_all": "/market",
}


async def _defer_update(interaction: discord.Interaction):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass


async def _resolve_user(interaction: discord.Interaction, user_id: int) -> discord.abc.User | None:
    """The member (for their server nickname) or user with this ID, or None if Discord no longer knows them."""
    member = interaction.guild.get_member(user_id) if interaction.guild is not None else None
    if member is not None:
        return member
    user = interaction.client.get_user(user_id)
    if user is not None:
        return user
    try:
        return await interaction.client.fetch_user(user_id)
    except discord.HTTPException:
        return None


async def send_expired_menu_notice(interaction: discord.Interaction, command: str):
    """Old /inventory or /market messages carry no paging state; point the clicker at a fresh command."""
    await interaction.response.send_message(f"This menu has expired. Run `{command}` again.", ephemeral=True)


async def refresh_leaderboard_message(interaction: discord.Interaction, period: str, days: int, sort_by: str = "kills"):
    await _defer_update(interaction)
    embed = await generate_leaderboard_embed(period=period, days=days, sort_by=sort_by)
    await interaction.message.edit(embed=embed, view=LeaderboardView(period, sort_by))


class LeaderboardSortSelect(discord.ui.Select):
    def __init__(self, selected_sort: str = "kills"):
        sort_key = selected_sort if selected_sort in LEADERBOARD_SORT_CONFIG else "kills"
        options = [
            discord.SelectOption(label=cfg["label"], value=key, default=(key == sort_key))
            for key, cfg in LEADERBOARD_SORT_CONFIG.items()
        ]
        super().__init__(
            placeholder="Select leaderboard stat",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="leaderboard_sort"
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class LeaderboardView(discord.ui.View):
    def __init__(self, initial_period: str = "Weekly", initial_sort: str = "kills"):
        super().__init__(timeout=None)
        self.initial_sort = initial_sort if initial_sort in LEADERBOARD_SORT_CONFIG else "kills"

        if initial_period == "Weekly":
            self.weekly_button.style = discord.ButtonStyle.primary
            self.monthly_button.style = discord.ButtonStyle.secondary
        else:
            self.weekly_button.style = discord.ButtonStyle.secondary
            self.monthly_button.style = discord.ButtonStyle.primary

        self.add_item(LeaderboardSortSelect(self.initial_sort))

    @discord.ui.button(label="Weekly", style=discord.ButtonStyle.primary, custom_id="leaderboard_weekly")
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id="leaderboard_monthly")
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


class SeasonalLeaderboardView(discord.ui.View):
    """Stat picker plus Prev/Next paging for /leaderboard_season; clicks are handled in log_interaction."""
    def __init__(self, month: str, page: int, total_pages: int, stat: str = "damage"):
        super().__init__(timeout=None)
        stat = normalize_season_stat(stat)
        # custom_ids carry the month, stat and target page, so the controls keep working after a restart.
        if total_pages > 1:
            self.add_item(discord.ui.Button(
                label="◀ Previous",
                style=discord.ButtonStyle.primary,
                custom_id=f"season_prev:{month}:{stat}:{max(0, page - 1)}",
                disabled=page <= 0,
                row=0,
            ))
            self.add_item(discord.ui.Button(
                label="Next ▶",
                style=discord.ButtonStyle.primary,
                custom_id=f"season_next:{month}:{stat}:{min(total_pages - 1, page + 1)}",
                disabled=page >= total_pages - 1,
                row=0,
            ))
        self.add_item(discord.ui.Select(
            placeholder="Rank by",
            options=[
                discord.SelectOption(label=label, value=key, emoji=emoji, default=(key == stat))
                for key, (label, emoji, _description, _format) in SEASON_STATS.items()
            ],
            custom_id=f"season_stat:{month}",
            row=1,
        ))


def parse_season_page_id(custom_id: str) -> tuple[str, str, int]:
    """(month, stat, page) from "season_prev:{month}:{stat}:{page}" (or "season_next:..."). Messages sent before
    the stat picker existed use "season_prev:{month}:{page}", which ranks by damage."""
    parts = custom_id.split(":")
    if len(parts) == 3:
        return parts[1], "damage", int(parts[2])
    _, month, stat, page = parts
    return month, normalize_season_stat(stat), int(page)


async def build_seasonal_leaderboard_message(guild_id: int, month: str | None = None, page: int = 0, stat: str = "damage"):
    """Returns (embed, view) for one page of a month's leaderboard ranked by `stat`."""
    embed, month_key, page, total_pages = await build_seasonal_leaderboard_payload(guild_id, month, page, stat)
    return embed, SeasonalLeaderboardView(month_key, page, total_pages, stat)


async def refresh_seasonal_leaderboard_message(interaction: discord.Interaction, month: str, page: int, stat: str = "damage"):
    await _defer_update(interaction)
    embed, view = await build_seasonal_leaderboard_message(interaction.guild_id, month, page, stat)
    await interaction.message.edit(embed=embed, view=view)


class QueueResultView(discord.ui.View):
    """Persistent view attached to queue-result messages, so anyone missing from the roster can self-serve
    a verification link (visible only to them, in the same channel) instead of having to know /verify exists.
    Finished queues also get a Game breakdown button (handled in log_interaction, like the other buttons)."""
    def __init__(self, include_breakdown: bool = False):
        super().__init__(timeout=None)
        if not include_breakdown:
            self.remove_item(self.breakdown_button)

    @discord.ui.button(label="Game breakdown", style=discord.ButtonStyle.primary, custom_id="queue_result_games")
    async def breakdown_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Account unlinked?  Verify here for a gf.", style=discord.ButtonStyle.secondary, custom_id="queue_result_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction (deferring here too would race it on the same interaction)
        pass


_queue_result_views: dict[bool, QueueResultView] = {}


def get_queue_result_view(include_breakdown: bool = False):
    """One shared view per variant (with or without the Game breakdown button), reused for every result message."""
    if include_breakdown not in _queue_result_views:
        _queue_result_views[include_breakdown] = QueueResultView(include_breakdown)
    return _queue_result_views[include_breakdown]


class QueueGamesPagerView(discord.ui.View):
    """Previous/Next buttons under a Game breakdown card; clicks are handled in log_interaction. The queue and the
    target page live in each custom_id (like the seasonal leaderboard), so nothing has to be kept in memory."""
    def __init__(self, match_id: str, page: int, total_pages: int):
        super().__init__(timeout=900)  # finite: these live in ephemeral messages, so don't hold them forever
        self.add_item(discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.primary,
            custom_id=f"qgame_prev:{match_id}:{max(0, page - 1)}",
            disabled=page <= 0,
        ))
        self.add_item(discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.primary,
            custom_id=f"qgame_next:{match_id}:{min(total_pages - 1, page + 1)}",
            disabled=page >= total_pages - 1,
        ))


def parse_game_page_id(custom_id: str) -> tuple[str, int]:
    """Splits "qgame_prev:{match_id}:{page}" / "qgame_next:..." into (match_id, page). The queue number is whatever
    sits between the first and last colon, so an unusual one can't break the page lookup."""
    _, rest = custom_id.split(":", 1)
    match_id, page = rest.rsplit(":", 1)
    return match_id, int(page)


async def build_queue_game_message(guild_id: int | None, match_id: str, page: int):
    """Returns (embed, file, view, None) for one game of a queue's breakdown, or (None, None, None, error_text).
    The view is None when the queue only has a single game."""
    embed, file, page, total_pages, error_text = await build_queue_game_payload(guild_id, match_id, page)
    if error_text:
        return None, None, None, error_text
    view = QueueGamesPagerView(match_id, page, total_pages) if total_pages > 1 else None
    return embed, file, view, None


async def show_queue_game_breakdown(interaction: discord.Interaction):
    """Game breakdown button: opens the first game as an ephemeral message only the clicker sees."""
    await interaction.response.defer(ephemeral=True)
    match_id = parse_queue_match_id(interaction.message)
    if match_id is None:
        await interaction.followup.send(queue_games_unavailable(None), ephemeral=True)
        return

    embed, file, view, error_text = await build_queue_game_message(interaction.guild_id, match_id, 0)
    if error_text:
        await interaction.followup.send(error_text, ephemeral=True)
        return
    options = {"view": view} if view is not None else {}  # followup.send rejects view=None
    await interaction.followup.send(embed=embed, file=file, ephemeral=True, **options)


async def refresh_queue_game_message(interaction: discord.Interaction, match_id: str, page: int):
    """Previous/Next buttons: swaps the ephemeral breakdown message over to another game."""
    await interaction.response.defer()
    embed, file, view, error_text = await build_queue_game_message(interaction.guild_id, match_id, page)
    if error_text:
        await interaction.followup.send(error_text, ephemeral=True)
        return
    # Ephemeral messages can only be edited through the interaction itself, not interaction.message.edit.
    await interaction.edit_original_response(embed=embed, attachments=[file], view=view)


class InventoryRaritySelect(discord.ui.Select):
    def __init__(self, target_id: int, selected_rarity: str = "all", rarity_counts: dict[str, int] | None = None):
        rarity_key = normalize_inventory_rarity_filter(selected_rarity)
        counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        options = [
            discord.SelectOption(
                label=f"{label} ({counts.get(value, 0)})",
                value=value,
                default=(value == rarity_key)
            )
            for value, label in INVENTORY_RARITY_FILTERS.items()
        ]
        super().__init__(
            placeholder="Select rarity",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"inv_rarity:{target_id}",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class InventoryPaginationView(discord.ui.View):
    """Previous/Next and a rarity filter for /inventory. custom_ids carry whose inventory, the filter and the
    target page ("inv_next:{user_id}:{rarity}:{page}", "inv_rarity:{user_id}")."""
    def __init__(self, target_id: int, page: int, total_pages: int, rarity_filter: str = "all",
                 rarity_counts: dict[str, int] | None = None):
        super().__init__(timeout=None)
        rarity_key = normalize_inventory_rarity_filter(rarity_filter)
        self.add_item(discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.primary,
            custom_id=f"inv_prev:{target_id}:{rarity_key}:{max(0, page - 1)}",
            disabled=page <= 0,
            row=0,
        ))
        self.add_item(discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.primary,
            custom_id=f"inv_next:{target_id}:{rarity_key}:{min(total_pages - 1, page + 1)}",
            disabled=page >= total_pages - 1,
            row=0,
        ))
        self.add_item(InventoryRaritySelect(target_id, rarity_key, rarity_counts))


def parse_inventory_page_id(custom_id: str) -> tuple[int, str, int]:
    """(user_id, rarity, page) from "inv_prev:{user_id}:{rarity}:{page}" or "inv_next:..."."""
    _, user_id, rarity, page = custom_id.split(":")
    return int(user_id), normalize_inventory_rarity_filter(rarity), int(page)


class ShopPaginationView(discord.ui.View):
    """Daily/Weekly/All toggles and paging for /market. custom_ids carry whose shop, the mode and the target page
    ("mkt_mode:{user_id}:{mode}", "mkt_next:{user_id}:{mode}:{page}")."""
    def __init__(self, target_id: int, page: int, total_pages: int, mode: str = "all"):
        super().__init__(timeout=None)
        mode = mode if mode in MARKET_MODES else "all"
        for key in MARKET_MODES:
            self.add_item(discord.ui.Button(
                label=key.capitalize(),
                style=discord.ButtonStyle.primary if key == mode else discord.ButtonStyle.secondary,
                custom_id=f"mkt_mode:{target_id}:{key}",
                row=0,
            ))
        self.add_item(discord.ui.Button(
            label="◀ Previous",
            style=discord.ButtonStyle.primary,
            custom_id=f"mkt_prev:{target_id}:{mode}:{max(0, page - 1)}",
            disabled=page <= 0,
            row=1,
        ))
        self.add_item(discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.primary,
            custom_id=f"mkt_next:{target_id}:{mode}:{min(total_pages - 1, page + 1)}",
            disabled=page >= total_pages - 1,
            row=1,
        ))


def parse_market_id(custom_id: str) -> tuple[int, str, int]:
    """(user_id, mode, page) from "mkt_prev/mkt_next:{user_id}:{mode}:{page}" or "mkt_mode:{user_id}:{mode}"
    (a mode switch always opens page 0)."""
    parts = custom_id.split(":")
    mode = parts[2] if parts[2] in MARKET_MODES else "all"
    page = int(parts[3]) if len(parts) > 3 else 0
    return int(parts[1]), mode, page


async def build_inventory_message(target_user: discord.abc.User, access_token: str, rarity_filter: str = "all",
                                  page: int = 0, use_cache: bool = False):
    """Returns (embed, file, view, None) for one page of a player's inventory, or (None, None, None, error_text)."""
    rarity_key = normalize_inventory_rarity_filter(rarity_filter)
    content, file, error_text, total_pages, filename, rarity_counts = await build_inventory_image_payload(
        target_user, access_token, page=page, rarity_filter=rarity_key, use_cache=use_cache
    )
    if error_text or not file:
        return None, None, None, error_text or "Failed to load inventory."

    total_pages = total_pages or 1
    page = max(0, min(page, total_pages - 1))
    embed = discord.Embed(title=f"{content} ({INVENTORY_RARITY_FILTERS[rarity_key]})", color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page {page + 1} of {total_pages}")
    return embed, file, InventoryPaginationView(target_user.id, page, total_pages, rarity_key, rarity_counts), None


async def build_market_message(target_user: discord.abc.User, access_token: str, mode: str = "all", page: int = 0,
                               use_cache: bool = False):
    """Returns (embed, file, view, None) for one page of a player's shop offers, or (None, None, None, error_text)."""
    mode = mode if mode in MARKET_MODES else "all"
    content, file, filename, error_text, total_pages = await build_shop_image_payload(
        target_user, access_token, mode=mode, page=page, use_cache=use_cache
    )
    if error_text or not file:
        return None, None, None, error_text or "Failed to load shop offers."

    page = max(0, min(page, total_pages - 1))
    embed = discord.Embed(title=content, color=discord.Color.blurple())
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=f"Page {page + 1} of {total_pages}")
    return embed, file, ShopPaginationView(target_user.id, page, total_pages, mode), None


async def _tell_clicker(interaction: discord.Interaction, text: str, ephemeral: bool = True):
    """Sends a message whether or not the click has been answered yet."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(text, ephemeral=ephemeral)


async def _show_page(interaction: discord.Interaction, file: discord.File, embed: discord.Embed, view):
    """Swaps the clicked message to the new page: as the click's answer when possible (one request to Discord),
    otherwise as an edit after the earlier defer."""
    if interaction.response.is_done():
        await interaction.message.edit(attachments=[file], embed=embed, view=view)
    else:
        await interaction.response.edit_message(attachments=[file], embed=embed, view=view)


async def _paged_target(interaction: discord.Interaction, target_id: int):
    """(user, token) for the player a paged message belongs to, after telling the clicker if either is gone."""
    access_token = get_user_token(target_id)
    if not access_token:
        await _tell_clicker(interaction, "That player's survev.de account is no longer linked.")
        return None, None
    target = await _resolve_user(interaction, target_id)
    if target is None:
        await _tell_clicker(interaction, "Couldn't find that player anymore.")
        return None, None
    return target, access_token


async def refresh_market_message(interaction: discord.Interaction, target_id: int, mode: str, page: int):
    """Shows another mode or page of a /market message, reusing the cached shop data when available. Like
    refresh_inventory_message, a click on cached data is answered with the edit itself."""
    if not has_cached_market(target_id):
        await interaction.response.defer()  # the offers have to be fetched from survev.de first
    target_user, access_token = await _paged_target(interaction, target_id)
    if target_user is None:
        return
    embed, file, view, error_text = await build_market_message(target_user, access_token, mode, page, use_cache=True)
    if error_text:
        await _tell_clicker(interaction, "Failed to reload shop offers.", ephemeral=False)
        return
    await _show_page(interaction, file, embed, view)


async def refresh_inventory_message(interaction: discord.Interaction, target_id: int, rarity_filter: str, page: int):
    """Shows another rarity or page of an /inventory message, reusing the cached inventory when available.
    With the inventory cached, the page is ready (usually pre-rendered) well inside Discord's 3-second window, so the
    click is answered with the edit itself: one request to Discord instead of a defer followed by an edit."""
    if not has_cached_inventory(target_id):
        await interaction.response.defer()  # the inventory has to be fetched from survev.de first
    target_user, access_token = await _paged_target(interaction, target_id)
    if target_user is None:
        return
    embed, file, view, error_text = await build_inventory_message(
        target_user, access_token, rarity_filter, page, use_cache=True
    )
    if error_text:
        await _tell_clicker(interaction, "Failed to reload inventory.", ephemeral=False)
        return
    await _show_page(interaction, file, embed, view)


PROFILE_TABS = ("overview", "history")


class ProfileView(discord.ui.View):
    """/profile's Overview / History buttons, plus Damage / Impact / Kills on the History view to switch its trend
    chart. Every custom_id carries the server and player, so the buttons keep working after a restart. The metric ids
    ("history_metric:{metric}:{guild_id}:{discord_id}") predate the merge, so buttons on old /history messages work too."""
    def __init__(self, guild_id: int, discord_id: int, tab: str = "overview", metric: str = "damage"):
        super().__init__(timeout=None)
        self.tab = tab if tab in PROFILE_TABS else "overview"
        self.active_metric = normalize_history_metric(metric)
        for key, label in (("overview", "Overview"), ("history", "History")):
            self.add_item(discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary if key == self.tab else discord.ButtonStyle.secondary,
                custom_id=f"profile_tab:{key}:{guild_id}:{discord_id}",
                row=0,
            ))
        if self.tab == "history":
            for key, label in (("damage", "Damage"), ("impact", "Impact"), ("kills", "Kills")):
                self.add_item(discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.primary if key == self.active_metric else discord.ButtonStyle.secondary,
                    custom_id=f"history_metric:{key}:{guild_id}:{discord_id}",
                    row=1,
                ))


def parse_profile_tab_id(custom_id: str) -> tuple[str, int, int]:
    """Splits 'profile_tab:{tab}:{guild_id}:{discord_id}' into (tab, guild_id, discord_id)."""
    _, tab, guild_id, discord_id = custom_id.split(":")
    return (tab if tab in PROFILE_TABS else "overview"), int(guild_id), int(discord_id)


def parse_history_metric_id(custom_id: str) -> tuple[str, int, int]:
    """Splits 'history_metric:{metric}:{guild_id}:{discord_id}' into (metric, guild_id, discord_id)."""
    _, metric, guild_id, discord_id = custom_id.split(":")
    return metric, int(guild_id), int(discord_id)


async def build_profile_message(target: discord.abc.User, guild_id: int | None, tab: str = "overview",
                                metric: str = "damage"):
    """Returns (embed, file, view, None) for one /profile view, or (None, None, None, message). file is only set on
    the History view; view is None when there is no history to switch to (e.g. outside a server)."""
    if tab == "history":
        embed, file, metric, message = await build_profile_history(target, guild_id, metric)
        if message:
            return None, None, None, message
        return embed, file, ProfileView(guild_id, target.id, "history", metric), None

    embed, has_history, message = await build_profile_overview(target, get_user_token(target.id), guild_id)
    if message:
        return None, None, None, message
    view = ProfileView(guild_id, target.id, "overview") if has_history else None
    return embed, None, view, None


async def refresh_profile_message(interaction: discord.Interaction, guild_id: int, discord_id: int,
                                  tab: str = "overview", metric: str = "damage"):
    """Switches a /profile message (or an old /history one) to another view or trend metric."""
    await _defer_update(interaction)

    target = await _resolve_user(interaction, discord_id)
    if target is None:
        await interaction.followup.send("Couldn't find that player anymore.", ephemeral=True)
        return

    embed, file, view, message = await build_profile_message(target, guild_id, tab, metric)
    if message:
        await interaction.followup.send(message, ephemeral=True)
        return
    await interaction.message.edit(embed=embed, attachments=[file] if file else [], view=view)
