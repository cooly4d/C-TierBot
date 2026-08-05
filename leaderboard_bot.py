import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import sqlite3
import json
from datetime import datetime, timedelta, timezone

# --- CONFIGURATION ---
DISCORD_BOT_TOKEN = ""
SURVEV_CLIENT_ID = ""
SURVEV_CLIENT_SECRET = ""
NEATQUEUE_API_TOKEN = ""  
NEATQUEUE_API_BASE = "https://api.neatqueue.com/api/v1"
NEATQUEUE_SERVER_ID = os.getenv("NEATQUEUE_SERVER_ID", "")  
#all supposed to be environment variables by cba


# Database initialization
conn = sqlite3.connect("leaderboard.db")
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        discord_id INTEGER PRIMARY KEY,
        access_token TEXT NOT NULL
    )
''')
conn.commit()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

# Helper: Save User Token
def save_token(discord_id: int, token: str):
    with sqlite3.connect("leaderboard.db") as c:
        c.execute("INSERT OR REPLACE INTO users (discord_id, access_token) VALUES (?, ?)", (discord_id, token))

# Helper: Get All User Tokens
def get_all_users():
    with sqlite3.connect("leaderboard.db") as c:
        return c.execute("SELECT discord_id, access_token FROM users").fetchall()

# Helper: Get Single User Token
def get_user_token(discord_id: int):
    with sqlite3.connect("leaderboard.db") as c:
        row = c.execute("SELECT access_token FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
        return row[0] if row else None


# ------------------------------------------------------------------
# 1. VERIFICATION COMMAND
# ------------------------------------------------------------------
@bot.tree.command(name="verify", description="Link your survev.de account to join the server leaderboards!")
async def verify(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    device_url = "https://survev.de/api/oauth/device/code"
    token_url = "https://survev.de/api/oauth/token"
    
    payload = {
        "clientId": SURVEV_CLIENT_ID,
        "clientSecret": SURVEV_CLIENT_SECRET,
        "scope": ["read:discord", "read:stats"]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(device_url, json=payload) as resp:
            if resp.status != 200:
                error_details = await resp.text()
                print(f"DEBUG - Survev Error Code {resp.status}: {error_details}")
                await interaction.followup.send(f"Failed to start authorization. Server responded ({resp.status}): `{error_details}`")
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
            await interaction.followup.send(embed=embed, ephemeral=True)

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
                        save_token(interaction.user.id, access_token)
                        await interaction.followup.send("✅ **Account Linked!** You are now entered into weekly & monthly leaderboards.", ephemeral=True)
                        break
                    
                    error = t_data.get("error")
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        await asyncio.sleep(2)
                    else:
                        await interaction.followup.send(f"❌ Authorization failed: `{error}`", ephemeral=True)
                        break


# ------------------------------------------------------------------
# 2. LEADERBOARD GENERATION ENGINE
# ------------------------------------------------------------------
async def fetch_player_timeframe_stats(session: aiohttp.ClientSession, access_token: str, from_ms: int, to_ms: int):
    """Pages through all available matches in the timeframe using offset."""
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

    if not all_matches:
        return {"games": 0, "wins": 0, "kills": 0, "damage": 0}

    return {
        "games": len(all_matches),
        "wins": sum(1 for m in all_matches if m.get("rank") == 1),
        "kills": sum(m.get("kills", 0) for m in all_matches),
        "damage": sum(m.get("damage_dealt", 0) for m in all_matches)
    }

async def generate_leaderboard_embed(period: str, days: int):
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

    # Sort by total Kills
    leaderboard_data.sort(key=lambda x: x["stats"]["kills"], reverse=True)

    embed = discord.Embed(
        title=f"🏆 Server {period} Leaderboard",
        description=f"Performance over the past **{days} days** (Sorted by Kills)",
        color=discord.Color.gold() if period == "Weekly" else discord.Color.purple()
    )

    if not leaderboard_data:
        embed.description = "No matches logged by verified members in this timeframe."
        return embed

    rank_emojis = ["🥇", "🥈", "🥉"]
    leaderboard_text = ""

    for idx, entry in enumerate(leaderboard_data[:10]): # Top 10
        rank = rank_emojis[idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        leaderboard_text += (
            f"{rank} <@{entry['discord_id']}>\n"
            f"┣ ⚔️ Kills: **{stats['kills']}** | 🏆 Wins: **{stats['wins']}**\n"
            f"┗ 🎮 Games: **{stats['games']}** | 💥 Damage: **{stats['damage']:,}**\n\n"
        )

    embed.add_field(name="Top Players", value=leaderboard_text, inline=False)
    embed.set_footer(text="Info courtesy of survev.de API :)")
    return embed


# ------------------------------------------------------------------
# 3. NEATQUEUE INTEGRATION ENGINE
# ------------------------------------------------------------------
async def fetch_neatqueue_history(session: aiohttp.ClientSession, match_number: str | None = None):
    """Fetches history for the configured NeatQueue server, optionally limited to one game number."""
    if not NEATQUEUE_SERVER_ID or NEATQUEUE_SERVER_ID == "YOUR_SERVER_ID_HERE":
        return None, "NeatQueue server ID is not configured. Set NEATQUEUE_SERVER_ID in the script or environment."

    url = f"{NEATQUEUE_API_BASE}/history/{NEATQUEUE_SERVER_ID}"
    params = None
    if match_number is not None:
        params = {
            "start_game_number": str(match_number),
            "end_game_number": str(match_number)
        }

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
                    f"Verify NEATQUEUE_API_BASE, NEATQUEUE_SERVER_ID, and match number; response detail: {error_detail}"
                )
            return None, f"NeatQueue API returned {resp.status} for URL {url}: {error_detail}"

        try:
            data = json.loads(text)
        except Exception as exc:
            return None, f"NeatQueue returned invalid JSON from {url}: {exc}"

        return data, None


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


def get_match_end_ms(match, default_start_ms: int):
    end_ms = get_nested_value(match, "end_time_ms")
    parsed_end = parse_neatqueue_time(end_ms)
    if parsed_end is not None:
        return parsed_end

    player_timestamps = []
    for player in match.get("players", []):
        timestamp_value = get_nested_value(player, "timestamp") or get_nested_value(player, "time")
        parsed = parse_neatqueue_time(timestamp_value)
        if parsed is not None:
            player_timestamps.append(parsed)

    if player_timestamps:
        return max(player_timestamps) + 5000
    return default_start_ms + 60000


def find_neatqueue_match(entries, match_id):
    match_id_str = str(match_id).lower()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in ("match_id", "id", "matchId", "game_id", "gameId", "server_match_id"):
            value = get_nested_value(entry, key)
            if value is not None and str(value).lower() == match_id_str:
                return entry
    return None


def collect_neatqueue_players(match):
    if not isinstance(match, dict):
        return []

    def collect(data):
        if isinstance(data, dict):
            if "id" in data and isinstance(data["id"], str):
                return [data]
            players = []
            for value in data.values():
                players.extend(collect(value))
            return players
        if isinstance(data, list):
            players = []
            for item in data:
                players.extend(collect(item))
            return players
        return []

    players = match.get("players")
    if players:
        return collect(players)

    teams = match.get("teams")
    if teams:
        return collect(teams)

    return []


async def calculate_queue_match_stats(match_id: str):
    """Cross-references one NeatQueue match from server history with survev.de stats."""
    async with aiohttp.ClientSession() as session:
        nq_data, error = await fetch_neatqueue_history(session, match_id)
        if error:
            return None, error
        if not nq_data:
            return None, "NeatQueue returned empty history data."

        entries = extract_neatqueue_entries(nq_data)
        if not entries:
            return None, "NeatQueue history contains no match entries."

        # NeatQueue returns the requested match as the first entry in the result.
        match = entries[0]

        start_ms = get_match_start_ms(match)
        if start_ms is None:
            return None, "NeatQueue match entry is missing a recognizable start time."

        end_ms = get_match_end_ms(match, start_ms)

        players = collect_neatqueue_players(match)
        if not players:
            return None, "NeatQueue match entry contains no player roster."

        results = []
        for player in players:
            p_id = player.get("id")
            try:
                discord_id = int(p_id)
            except (TypeError, ValueError):
                continue

            token = get_user_token(discord_id)
            if not token:
                continue  # Skip unverified players

            stats = await fetch_player_timeframe_stats(session, token, start_ms, end_ms)
            if stats:
                results.append({
                    "discord_id": discord_id,
                    "stats": stats
                })

        return results, None


# ------------------------------------------------------------------
# 4. SLASH COMMANDS
# ------------------------------------------------------------------
@bot.tree.command(name="leaderboard_weekly", description="View the top players over the past 7 days.")
async def leaderboard_weekly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed(period="Weekly", days=7)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="leaderboard_monthly", description="View the top players over the past 30 days.")
async def leaderboard_monthly(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = await generate_leaderboard_embed(period="Monthly", days=30)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="queue_stats", description="Calculate player damage and stats for a specific NeatQueue match number.")
@discord.app_commands.describe(match_id="The NeatQueue game number to pull stats for")
async def queue_stats(interaction: discord.Interaction, match_id: str):
    await interaction.response.defer()
    
    results, error = await calculate_queue_match_stats(match_id)
    if error:
        await interaction.followup.send(f"❌ {error}")
        return

    if not results:
        await interaction.followup.send("No verified players were found in this NeatQueue match, or no games were logged during the time frame.")
        return

    # Sort results by damage dealt
    results.sort(key=lambda x: x["stats"]["damage"], reverse=True)

    embed = discord.Embed(
        title=f"🎮 NeatQueue Match Breakdown (Match #{match_id})",
        description="Stats logged during this queue match window:",
        color=discord.Color.teal()
    )

    for idx, entry in enumerate(results):
        rank = ["🥇", "🥈", "🥉"][idx] if idx < 3 else f"`#{idx+1}`"
        stats = entry["stats"]
        embed.add_field(
            name=f"{rank} Player",
            value=f"<@{entry['discord_id']}>\n"
                  f"💥 Damage: **{stats['damage']:,}**\n"
                  f"⚔️ Kills: **{stats['kills']}** | 🏆 Wins: **{stats['wins']}**\n"
                  f"🎮 Games Played: **{stats['games']}**",
            inline=False
        )

    embed.set_footer(text="Data synchronized via NeatQueue & survev.de APIs")
    await interaction.followup.send(embed=embed)


# --- RUN BOT ---
bot.run(DISCORD_BOT_TOKEN)