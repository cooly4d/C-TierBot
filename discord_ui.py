"""Discord UI components: persistent Views/Selects for the leaderboard, queue-result
verify button, and inventory/shop pagination, plus the message-refresh handlers they trigger.

Note: the actual click/select dispatch lives in `log_interaction` (leaderboard_bot.py) —
these views' own callbacks are mostly no-ops (see the comment on QueueResultView).
"""
import aiohttp
import discord

from bot_config import LEADERBOARD_SORT_CONFIG
from image_utils import (
    INVENTORY_RARITY_FILTERS,
    SHOP_ITEMS_PER_PAGE,
    INVENTORY_ITEMS_PER_PAGE,
    build_inventory_rarity_counts,
    generate_inventory_image,
    generate_shop_image,
    group_inventory_items,
    normalize_inventory_rarity_filter,
)
from leaderboard_service import generate_leaderboard_embed
from survev_client import fetch_user_inventory, fetch_user_market, run_survev_verification

# Store pagination state: msg_id -> (target_user, access_token, mode/rarity, current_page, total_pages)
inventory_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}
market_pagination_state: dict[int, tuple[discord.User, str, str, int, int]] = {}


async def refresh_leaderboard_message(interaction: discord.Interaction, period: str, days: int, sort_by: str = "kills"):
    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
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
        period = "Weekly"
        days = 7
        if isinstance(self.view, LeaderboardView):
            period = self.view.initial_period
            days = 30 if period == "Monthly" else 7
        selected_sort = self.values[0] if self.values else "kills"
        await refresh_leaderboard_message(interaction, period, days, selected_sort)


class LeaderboardView(discord.ui.View):
    def __init__(self, initial_period: str = "Weekly", initial_sort: str = "kills"):
        super().__init__(timeout=None)
        self.initial_period = initial_period
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
        await refresh_leaderboard_message(interaction, "Weekly", 7, self.initial_sort)

    @discord.ui.button(label="Monthly", style=discord.ButtonStyle.secondary, custom_id="leaderboard_monthly")
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await refresh_leaderboard_message(interaction, "Monthly", 30, self.initial_sort)


class QueueResultView(discord.ui.View):
    """Persistent view attached to queue-result messages, so anyone missing from the roster can self-serve
    a verification link (visible only to them, in the same channel) instead of having to know /verify exists."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Account unlinked?  Verify here for a gf.", style=discord.ButtonStyle.secondary, custom_id="queue_result_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        print(f"DEBUG - verify_button callback invoked by {interaction.user}")
        await interaction.response.defer(ephemeral=True)
        print("DEBUG - verify_button deferred, starting verification flow")
        await run_survev_verification(interaction.user.id, lambda **kw: interaction.followup.send(ephemeral=True, **kw))
        print("DEBUG - verify_button verification flow finished")

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        # discord.py's default View.on_error just logs to stderr, which is easy to miss — surface it
        # to the clicking user too so a failure is never silent.
        print(f"ERROR - QueueResultView item {item} failed: {error!r}")
        message = f"❌ Something went wrong: `{error}`"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


queue_result_view = None


def get_queue_result_view():
    global queue_result_view
    if queue_result_view is None:
        queue_result_view = QueueResultView()
    return queue_result_view


class InventoryRaritySelect(discord.ui.Select):
    def __init__(self, selected_rarity: str = "all", rarity_counts: dict[str, int] | None = None):
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
            custom_id="inventory_rarity"
        )

    async def callback(self, interaction: discord.Interaction):
        # Handled by log_interaction
        pass


class InventoryPaginationView(discord.ui.View):
    """Persistent view for paginating through inventory items."""
    def __init__(self, total_pages: int, rarity_filter: str = "all", rarity_counts: dict[str, int] | None = None):
        super().__init__(timeout=None)
        self.total_pages = total_pages
        self.rarity_filter = normalize_inventory_rarity_filter(rarity_filter)
        self.rarity_counts = rarity_counts or {key: 0 for key in INVENTORY_RARITY_FILTERS}
        self.add_item(InventoryRaritySelect(self.rarity_filter, self.rarity_counts))

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="inventory_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="inventory_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


class ShopPaginationView(discord.ui.View):
    """Persistent view for paginating through shop offers."""
    def __init__(self, total_pages: int):
        super().__init__(timeout=None)
        self.total_pages = total_pages

    def update_button_states(self, current_page: int):
        self.prev_button.disabled = current_page <= 0
        self.next_button.disabled = current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="market_prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="market_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Handled by log_interaction
        pass


async def refresh_market_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, mode: str, page: int, msg_id: int):
    """Refresh the market message with a new page."""
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        market_data, error = await fetch_user_market(session, access_token)
        if error or not market_data:
            await interaction.followup.send("Failed to reload shop offers.")
            return

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
        
        # Clamp page to valid range
        page = max(0, min(page, total_pages - 1))
        
        # Update state
        market_pagination_state[msg_id] = (target_user, access_token, mode, page, total_pages)
        
        # Generate new image
        image_buffer = generate_shop_image(username, market_data, mode, page)
        filename = f"shop_{target_user.id}_{mode}_p{page}.png"
        file = discord.File(image_buffer, filename=filename)
        
        # Create embed
        mode_label = "Daily" if mode == "daily" else "Weekly" if mode == "weekly" else "All"
        embed = discord.Embed(title=f"{username}'s {mode_label} Offers", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        
        # Create view with updated button states
        view = ShopPaginationView(total_pages)
        view.update_button_states(page)
        
        await interaction.message.edit(attachments=[file], embed=embed, view=view)


async def refresh_inventory_message(interaction: discord.Interaction, target_user: discord.User, access_token: str, rarity_filter: str, page: int, msg_id: int):
    """Refresh the inventory message with a new page."""
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        inventory, error = await fetch_user_inventory(session, access_token)
        if error or not inventory:
            await interaction.followup.send("Failed to reload inventory.")
            return

        items = inventory.get("items", [])
        username = inventory.get("username") or str(target_user)
        rarity_key = normalize_inventory_rarity_filter(rarity_filter)
        grouped_items = group_inventory_items(items)
        rarity_counts = build_inventory_rarity_counts(grouped_items)
        if rarity_key != "all":
            rarity_value = int(rarity_key)
            grouped_items = [entry for entry in grouped_items if int(entry.get("rarity", -1)) == rarity_value]
        total_pages = max(1, -(-len(grouped_items) // INVENTORY_ITEMS_PER_PAGE))
        
        # Clamp page to valid range
        page = max(0, min(page, total_pages - 1))
        
        # Update state
        inventory_pagination_state[msg_id] = (target_user, access_token, rarity_key, page, total_pages)
        
        # Generate new image
        image_buffer = generate_inventory_image(username, items, page, rarity_key)
        filename = f"inventory_{target_user.id}_p{page}.png"
        file = discord.File(image_buffer, filename=filename)
        
        # Create embed with image attached
        rarity_label_text = INVENTORY_RARITY_FILTERS[rarity_key]
        embed = discord.Embed(title=f"{username}'s Inventory ({rarity_label_text})", color=discord.Color.blurple())
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        
        # Create view with updated button states
        view = InventoryPaginationView(total_pages, rarity_key, rarity_counts)
        view.update_button_states(page)
        
        await interaction.message.edit(attachments=[file], embed=embed, view=view)
