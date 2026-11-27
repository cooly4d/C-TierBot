"""Host console interface for C-TierBot.

Runs an interactive, non-blocking command listener on the host terminal
so the bot owner can send messages, inspect servers/channels, view metrics,
run database queries, and control the bot directly from stdin without
relying on Discord interactions.
"""
import asyncio
import builtins
import contextvars
import io
import json
import logging
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import discord
from discord.ext import commands

from db import DB_PATH
from panel_sql import format_table
from panel_sql import run_readonly_sql  # noqa: F401 - the panel's sql, in-process for tests

_START_TIME = time.time()

log = logging.getLogger(__name__)

# Where console output goes. None means the real stdout; a socket session sets its own buffer, so a
# command typed through console.py prints back to that client instead of the container log.
_console_output: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar("console_output", default=None)


def print(*args, **kwargs):  # noqa: A001 - shadows the builtin on purpose, for every handler in this module
    stream = _console_output.get()
    if stream is not None and "file" not in kwargs:
        kwargs["file"] = stream
    builtins.print(*args, **kwargs)


_format_table = format_table


async def _handle_send(bot: commands.Bot, raw_args: str):
    """Usage: send <channel_id> <message>"""
    parts = raw_args.split(None, 1)
    if len(parts) < 2:
        print("[Console Error] Usage: send <channel_id> <message>")
        return

    channel_id_str, message_text = parts[0], parts[1]
    if not channel_id_str.isdigit():
        print(f"[Console Error] Invalid channel ID: {channel_id_str}")
        return

    channel_id = int(channel_id_str)
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[Console Error] Could not find or fetch channel {channel_id}: {exc}")
            return

    if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
        print(f"[Console Error] Channel {channel_id} ({type(channel).__name__}) cannot receive text messages.")
        return

    try:
        sent_msg = await channel.send(message_text)
        guild_name = channel.guild.name if hasattr(channel, "guild") and channel.guild else "Direct/Unknown"
        print(f"[Console] ✅ Sent message (ID: {sent_msg.id}) to #{channel.name} in '{guild_name}'")
    except Exception as exc:
        print(f"[Console Error] Failed to send message: {exc}")


async def _handle_dm(bot: commands.Bot, raw_args: str):
    """Usage: dm <user_id> <message>"""
    parts = raw_args.split(None, 1)
    if len(parts) < 2:
        print("[Console Error] Usage: dm <user_id> <message>")
        return

    user_id_str, message_text = parts[0], parts[1]
    if not user_id_str.isdigit():
        print(f"[Console Error] Invalid user ID: {user_id_str}")
        return

    user_id = int(user_id_str)
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception as exc:
            print(f"[Console Error] Could not find or fetch user {user_id}: {exc}")
            return

    try:
        sent_msg = await user.send(message_text)
        print(f"[Console] ✅ DM sent (ID: {sent_msg.id}) to @{user.name} ({user.id})")
    except Exception as exc:
        print(f"[Console Error] Failed to send DM (user may have DMs closed or blocked): {exc}")


async def _handle_reply(bot: commands.Bot, raw_args: str):
    """Usage: reply <channel_id> <message_id> <message>"""
    parts = raw_args.split(None, 2)
    if len(parts) < 3:
        print("[Console Error] Usage: reply <channel_id> <message_id> <message>")
        return

    channel_id_str, message_id_str, reply_text = parts[0], parts[1], parts[2]
    if not channel_id_str.isdigit() or not message_id_str.isdigit():
        print("[Console Error] Both channel_id and message_id must be numeric IDs.")
        return

    channel_id = int(channel_id_str)
    message_id = int(message_id_str)

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[Console Error] Could not fetch channel {channel_id}: {exc}")
            return

    try:
        target_message = await channel.fetch_message(message_id)
        sent_reply = await target_message.reply(reply_text)
        print(f"[Console] ✅ Replied to message {message_id} in #{channel.name} (Reply ID: {sent_reply.id})")
    except Exception as exc:
        print(f"[Console Error] Failed to fetch target message or send reply: {exc}")


async def _handle_delete(bot: commands.Bot, raw_args: str):
    """Usage: delete <channel_id> <message_id>"""
    parts = raw_args.split()
    if len(parts) != 2:
        print("[Console Error] Usage: delete <channel_id> <message_id>")
        return

    channel_id_str, message_id_str = parts[0], parts[1]
    if not channel_id_str.isdigit() or not message_id_str.isdigit():
        print("[Console Error] Both channel_id and message_id must be numeric IDs.")
        return

    channel_id = int(channel_id_str)
    message_id = int(message_id_str)

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            print(f"[Console Error] Could not fetch channel {channel_id}: {exc}")
            return

    try:
        target_message = await channel.fetch_message(message_id)
    except Exception as exc:
        print(f"[Console Error] Failed to fetch message {message_id}: {exc}")
        return

    if bot.user is not None and target_message.author.id != bot.user.id:
        print(f"[Console Error] Message {message_id} was not sent by the bot.")
        return

    try:
        await target_message.delete()
        channel_label = f"#{channel.name}" if hasattr(channel, "name") else f"channel {channel_id}"
        print(f"[Console] ✅ Deleted bot message {message_id} in {channel_label}")
    except Exception as exc:
        print(f"[Console Error] Failed to delete message {message_id}: {exc}")


def _handle_servers(bot: commands.Bot):
    """Lists all servers/guilds the bot is currently in."""
    guilds = bot.guilds
    if not guilds:
        print("[Console] The bot is not currently in any servers.")
        return

    print(f"\n[Console] Connected Servers ({len(guilds)} total):")
    headers = ["#", "Server Name", "Guild ID", "Members"]
    rows = []
    for idx, guild in enumerate(guilds, 1):
        rows.append([str(idx), guild.name, str(guild.id), str(guild.member_count)])
    print(_format_table(headers, rows))
    print()


async def _handle_channels(bot: commands.Bot, raw_args: str):
    """Usage: channels <guild_id>"""
    guild_id_str = raw_args.strip()
    if not guild_id_str.isdigit():
        print("[Console Error] Usage: channels <guild_id>")
        return

    guild_id = int(guild_id_str)
    guild = bot.get_guild(guild_id)
    if guild is None:
        try:
            guild = await bot.fetch_guild(guild_id)
        except Exception as exc:
            print(f"[Console Error] Could not find guild with ID {guild_id}: {exc}")
            return

    text_channels = [ch for ch in guild.channels if isinstance(ch, (discord.TextChannel, discord.Thread))]
    if not text_channels:
        print(f"[Console] No accessible text channels found in '{guild.name}'.")
        return

    print(f"\n[Console] Text Channels in '{guild.name}' ({len(text_channels)} channels):")
    headers = ["Name", "Channel ID", "Category"]
    rows = []
    for ch in text_channels:
        category_name = ch.category.name if getattr(ch, "category", None) else "(none)"
        rows.append([f"#{ch.name}", str(ch.id), category_name])
    print(_format_table(headers, rows))
    print()


def _status_counts() -> tuple[int, int]:
    with closing(sqlite3.connect(DB_PATH, timeout=1)) as conn:
        return (conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM processed_matches").fetchone()[0])


async def _handle_status(bot: commands.Bot):
    """Displays bot uptime, ping, and DB stats."""
    uptime_sec = int(time.time() - _START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"
    latency_ms = round(bot.latency * 1000, 2) if bot.latency else "N/A"

    # Quick DB counts, in a worker thread with a short lock wait, so a held database lock never stalls the bot.
    user_count = 0
    match_count = 0
    try:
        user_count, match_count = await asyncio.to_thread(_status_counts)
    except Exception as exc:
        print(f"[Console Warning] DB query failed during status check: {exc}")

    print("\n[Console] === Bot Status ===")
    print(f"  User:         {bot.user} (ID: {bot.user.id if bot.user else 'Unknown'})")
    print(f"  Websocket:    {latency_ms} ms")
    print(f"  Uptime:       {uptime_str}")
    print(f"  Servers:      {len(bot.guilds)}")
    print(f"  Linked Users: {user_count}")
    print(f"  Matches Logged:{match_count}")
    print("===========================\n")


def _handle_sql(raw_args: str):
    """Usage: sql <query>"""
    query = raw_args.strip()
    if not query:
        print("[Console Error] Usage: sql <SQL_QUERY>")
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(query)

            if query.strip().upper().startswith("SELECT"):
                rows = cursor.fetchall()
                if not rows:
                    print("[Console] Query returned 0 rows.")
                    return
                headers = [desc[0] for desc in cursor.description] if cursor.description else []
                string_rows = [[str(item) if item is not None else "NULL" for item in row] for row in rows]
                print(f"\n{_format_table(headers, string_rows)}\n({len(rows)} rows)\n")
            else:
                conn.commit()
                print(f"[Console] ✅ Query executed. Rows affected: {cursor.rowcount}")
    except Exception as exc:
        print(f"[Console SQL Error] {exc}")


async def _handle_sync(bot: commands.Bot):
    """Manually forces slash command synchronization."""
    print("[Console] Synchronizing slash commands with Discord...")
    try:
        synced = await bot.tree.sync()
        print(f"[Console] ✅ Successfully synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"[Console Error] Failed to sync slash commands: {exc}")


def _resolve_console_guild_id(bot: commands.Bot, guild_str: str | None = None) -> int | None:
    """Resolves a target guild_id from an explicit CLI argument, connected bot guilds, or guild_settings."""
    if guild_str:
        return int(guild_str) if guild_str.isdigit() else None
    if getattr(bot, "guilds", None):
        return bot.guilds[0].id
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT guild_id FROM guild_settings LIMIT 1").fetchone()
            if row:
                return row[0]
    except Exception:
        pass
    return None


async def _handle_queue(bot: commands.Bot, raw_args: str):
    """Inspects and calculates stats + diagnostics for a NeatQueue match without posting to Discord.
    Usage: queue <match_id> [guild_id]
    """
    parts = raw_args.split()
    if not parts:
        print("[Console Error] Usage: queue <match_id> [guild_id]")
        return

    match_id = parts[0].lstrip("#")
    if not match_id:
        print("[Console Error] Usage: queue <match_id> [guild_id]")
        return

    if len(parts) > 1 and not parts[1].isdigit():
        print(f"[Console Error] Invalid guild ID: {parts[1]}")
        return

    guild_id = _resolve_console_guild_id(bot, parts[1] if len(parts) > 1 else None)
    if not guild_id:
        print("[Console Error] No guild ID found. Usage: queue <match_id> <guild_id>")
        return

    from image_utils import format_duration_ms
    from neatqueue_client import calculate_queue_match_stats, resolve_queue_user_display_names

    diagnostics: dict = {}
    print(f"[Console] Inspecting Queue#{match_id} for guild {guild_id}...")
    match_result, error = await calculate_queue_match_stats(match_id, guild_id, diagnostics=diagnostics)

    window = diagnostics.get("window") or {}
    nq = diagnostics.get("neatqueue") or {}
    anchors = diagnostics.get("anchors") or []
    is_final = bool((match_result or {}).get("is_final", diagnostics.get("is_final")))
    status_label = "FINAL" if is_final else "IN PROGRESS"

    if error or not match_result:
        print(f"[Console Error] Queue#{match_id} lookup failed: {error}")
        print(f"  Status:          {status_label} ({diagnostics.get('finality_reason', 'unknown')})")
        print(f"  Roster Source:   {diagnostics.get('roster_source', 'none')}")
        print(f"  Window:          {window.get('from_utc', 'N/A')} -> {window.get('to_utc', 'N/A')}")
        print(f"  Anchors Queried: {len(anchors)}")
        return

    teams = match_result.get("teams") or []
    await resolve_queue_user_display_names(teams, bot)

    round_wins = match_result.get("team_round_wins") or []
    score_str = " - ".join(str(w) for w in round_wins) if round_wins else "N/A"
    winner_idx = match_result.get("winning_team_index")
    winner_str = f"Team {winner_idx + 1}" if winner_idx is not None else "Undecided / Tie"
    duration_ms = match_result.get("duration_ms") or 0

    print(f"\n[Console] === Queue #{match_id} Diagnostics (Guild: {guild_id}) ===")
    print(f"  Status:          {status_label} ({diagnostics.get('finality_reason', 'unknown')}, NQ winner={nq.get('winner')})")
    print(f"  Roster Source:   {diagnostics.get('roster_source', 'unknown')}")
    print(f"  Window:          {window.get('from_utc', 'N/A')} -> {window.get('to_utc', 'N/A')} "
          f"({window.get('start_source', '?')} -> {window.get('end_source', '?')})")
    print(f"  Duration:        {format_duration_ms(duration_ms)}")
    print(f"  Anchors:         {len(anchors)} queried ({diagnostics.get('selection_strategy', 'N/A')})")
    print(f"  Games Selected:  {match_result.get('total_games_played', 0)} ({', '.join(diagnostics.get('selected_guids') or [])})")
    print(f"  Series Score:    {score_str} (Winner: {winner_str})")

    headers = ["Team", "Player", "Account", "Games", "Kills", "Damage", "Avg", "Impact"]
    rows = []
    for team_idx, team in enumerate(teams, 1):
        for entry in team:
            stats = entry.get("stats") or {}
            games = int(stats.get("games") or 0)
            dmg = int(stats.get("damage") or 0)
            avg = f"{dmg / games:,.0f}" if games > 0 else "0"
            imp = stats.get("impact")
            imp_str = f"{int(imp + 0.5)}" if imp is not None else "-"
            acct = "Guest" if entry.get("guest") else f"Linked ({entry.get('slug') or entry.get('discord_id')})"
            rows.append([
                f"Team {team_idx}",
                entry.get("display_name") or entry.get("username") or "Unknown",
                acct,
                str(games),
                str(stats.get("kills") or 0),
                f"{dmg:,}",
                avg,
                imp_str,
            ])
    print("\n" + _format_table(headers, rows))
    print("====================================================\n")


async def _handle_winrate(bot: commands.Bot, raw_args: str):
    """Calculates and displays Team 1 vs Team 2 winrate across NeatQueue match history.
    Usage: winrate [guild_id]
    """
    guild_id = None
    target_str = raw_args.strip()
    if target_str.isdigit():
        guild_id = int(target_str)
    elif bot.guilds:
        guild_id = bot.guilds[0].id
    else:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute("SELECT guild_id FROM guild_settings LIMIT 1").fetchone()
                if row:
                    guild_id = row[0]
        except Exception:
            pass

    if not guild_id:
        print("[Console Error] No guild ID found. Usage: winrate <guild_id>")
        return

    print(f"[Console] Fetching NeatQueue history for guild {guild_id}...")
    from http_client import http_session
    from neatqueue_client import fetch_neatqueue_history

    t1, t2, other, total = 0, 0, 0, 0
    page = 1
    session = http_session
    while True:
        data, error = await fetch_neatqueue_history(session, guild_id, extra_params={"page_size": "1000", "page": str(page)})
        if error or not data:
            if page == 1 and error:
                print(f"[Console Error] Failed to fetch NeatQueue data: {error}")
                return
            break
        matches = data.get("data", [])
        if not matches:
            break
        for m in matches:
            w = m.get("winner")
            if w == 0:
                t1 += 1
            elif w == 1:
                t2 += 1
            else:
                other += 1
            total += 1
        pagination = data.get("pagination") or {}
        total_pages = pagination.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    decided = t1 + t2
    print(f"\n[Console] === NeatQueue Team Winrates (Guild: {guild_id}) ===")
    print(f"  Total Matches:    {total}")
    if decided > 0:
        p1 = (t1 / decided) * 100
        p2 = (t2 / decided) * 100
        print(f"  Team 1:           {t1} wins ({p1:.1f}%)")
        print(f"  Team 2:           {t2} wins ({p2:.1f}%)")
        if p1 > p2:
            print(f"  -> Team 1 has the higher winrate (+{t1 - t2} wins)")
        elif p2 > p1:
            print(f"  -> Team 2 has the higher winrate (+{t2 - t1} wins)")
        else:
            print("  -> Teams are tied (50.0% - 50.0%)")
    else:
        print("  No decided matches found.")
    if other > 0:
        print(f"  Tied / Other:     {other}")
    print("====================================================\n")


def _handle_help():
    """Prints command list and syntax."""
    help_text = """
=== C-TierBot Host Console Commands ===
  send <channel_id> <message>          - Send a message to a channel
  dm <user_id> <message>               - Send a private direct message to a user
  reply <channel_id> <msg_id> <text>   - Reply to a specific message in a channel
  delete <channel_id> <msg_id>         - Delete a message sent by the bot
  servers                              - List all servers the bot is in
  channels <guild_id>                  - List all text channels in a server
  status                               - View bot uptime, ping, and DB metrics
  sql <query>                          - Execute an SQL query on leaderboard.db
  winrate [guild_id]                   - Show NeatQueue Team 1 vs Team 2 winrate
  queue <match_id> [guild_id]          - Inspect queue stats and lookup diagnostics
  sync                                 - Force sync application slash commands
  clear                                - Clear the terminal screen
  help                                 - Show this help menu
  stop / exit / quit                   - Gracefully disconnect the bot and exit
                                         (in console.py, exit/quit only leave the prompt)
========================================
"""
    print(help_text)


async def execute_console_command(bot: commands.Bot, line: str):
    """Parses and routes a single line of console input."""
    trimmed = line.strip()
    if not trimmed:
        return

    parts = trimmed.split(None, 1)
    command_name = parts[0].lower()
    raw_args = parts[1] if len(parts) > 1 else ""

    if command_name == "help":
        _handle_help()
    elif command_name == "send":
        await _handle_send(bot, raw_args)
    elif command_name == "dm":
        await _handle_dm(bot, raw_args)
    elif command_name == "reply":
        await _handle_reply(bot, raw_args)
    elif command_name == "delete":
        await _handle_delete(bot, raw_args)
    elif command_name in ("servers", "guilds"):
        _handle_servers(bot)
    elif command_name == "channels":
        await _handle_channels(bot, raw_args)
    elif command_name == "status":
        await _handle_status(bot)
    elif command_name == "sql":
        _handle_sql(raw_args)
    elif command_name in ("winrate", "winrates"):
        await _handle_winrate(bot, raw_args)
    elif command_name in ("queue", "queue_stats"):
        await _handle_queue(bot, raw_args)
    elif command_name == "sync":
        await _handle_sync(bot)
    elif command_name == "clear":
        os.system("cls" if os.name == "nt" else "clear")
    elif command_name in ("exit", "quit", "stop"):
        print("[Console] Shutting down bot gracefully...")
        await bot.close()
    else:
        print(f"[Console] Unknown command: '{command_name}'. Type 'help' for available commands.")


async def start_console_listener(bot: commands.Bot):
    """Background task reading stdin asynchronously without blocking the event loop."""
    await bot.wait_until_ready()

    # If stdin is completely unavailable (e.g. background daemon/systemd where stdin is
    # /dev/null or closed), readline returns immediate EOF which would spin a CPU loop.
    # We do NOT check isatty() because stdin is still readable in SSH, screen, and tmux
    # sessions even though those aren't technically TTYs.
    if sys.stdin is None or sys.stdin.closed:
        return

    print("\n" + "=" * 50)
    print(" [Host Console] Ready! Type 'help' for available commands.")
    print("=" * 50 + "\n")

    while not bot.is_closed():
        try:
            # sys.stdin.readline is run in a separate worker thread via asyncio.to_thread
            # so the Discord event loop continues running smoothly without interruption.
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                # EOF reached (e.g. stream closed)
                await asyncio.sleep(1)
                continue

            await execute_console_command(bot, line)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"[Console Loop Error] {exc}")
            await asyncio.sleep(0.5)


async def run_console_command_captured(bot: commands.Bot, line: str) -> str:
    """Run one console command and return everything it printed, instead of writing it to stdout."""
    buffer = io.StringIO()
    token = _console_output.set(buffer)
    try:
        await execute_console_command(bot, line)
    except Exception as exc:
        print(f"[Console Error] {exc}")
    finally:
        _console_output.reset(token)
    return buffer.getvalue()


async def start_console_socket_server(bot: commands.Bot, port: int) -> asyncio.AbstractServer | None:
    """Serve console commands to console.py on 127.0.0.1:<port>.

    Each connection sends one command line and gets that command's output back, then the connection
    closes. Commands run one at a time. Loopback only: inside a container nothing outside it can reach
    the port, so anyone who can use it already has a shell in the container."""
    lock = asyncio.Lock()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            raw = await reader.readline()
            line = raw.decode("utf-8", errors="replace")
            async with lock:
                output = await run_console_command_captured(bot, line)
            writer.write(output.encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            log.warning("Console socket session failed: %r", exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(handle_client, host="127.0.0.1", port=port)
    except OSError as exc:
        log.warning("Console socket could not listen on 127.0.0.1:%s: %r", port, exc)
        return None
    log.info("Console socket listening on 127.0.0.1:%s (run: python console.py)", port)
    return server


# --- Operator panel console ------------------------------------------------------------------------------------
# The website's /admin panel runs the same commands as this console, one at a time, with two differences: `sql` is
# read-only (users.access_token always reads as NULL), and `stop` asks for `stop confirm` first. The website
# never imports this module (it would create the Discord client); the bot hands it a PanelConsole instead.

PANEL_COMMAND_SECONDS = 90   # under Cloudflare's 100-second proxy timeout
PANEL_MAX_LINE = 2100        # a 2,000-character Discord message plus its command and IDs
PANEL_MAX_OUTPUT = 200_000
PANEL_STOP_DELAY = 0.5       # after the website has sent the "Stopping" reply, for it to reach the browser
PANEL_STOP_FALLBACK = 15.0   # if the website never reports the reply sent, stop anyway
PANEL_SQL_SECONDS = 2.0      # the website's own job limit, so a query holds the database no longer than a page
PANEL_SQL_MAX_ROWS = 200

PANEL_COMMANDS = (
    ("help", "Show these commands"),
    ("status", "Bot latency, uptime and database counts"),
    ("servers", "Servers the bot is in"),
    ("channels <guild_id>", "A server's text channels and their IDs"),
    ("queue <match_id> [guild_id]", "A queue's stats and lookup diagnostics, without posting to Discord"),
    ("winrate [guild_id]", "Team 1 against Team 2 across NeatQueue history"),
    ("sql <query>", "One read-only query: 2-second limit, first 200 rows, access tokens read as NULL"),
    ("send <channel_id> <message>", "Send a message to a channel"),
    ("reply <channel_id> <message_id> <message>", "Reply to a message"),
    ("dm <user_id> <message>", "Send a direct message"),
    ("delete <channel_id> <message_id>", "Delete a message the bot sent"),
    ("sync", "Sync slash commands with Discord"),
    ("stop", "Stop the bot; Coolify starts it again. Asks you to confirm"),
)

# Panel `sql` runs in a process of its own (panel_sql.py): killed outright at its time limit, and on Linux limited in
# CPU time, memory and temporary-file size, so a query never holds the bot's threads or memory. Its environment
# carries no secrets: only what Python needs to start.
_SQL_WORKER = Path(__file__).resolve().with_name("panel_sql.py")
_SQL_WORKER_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
PANEL_SQL_GRACE = 3.0          # the worker's start-up and connect, on top of the query's own limit
PANEL_SQL_MAX_BYTES = 4 << 20  # what the panel reads back from it


_PANEL_PREFIXES = (
    ("[Console SQL Error] ", "SQL error: ", "err"),
    ("[Console Error] ", "Error: ", "err"),
    ("[Console Warning] ", "", "warn"),
    ("[Console] ", "", ""),
)


def panel_lines(text: str, limit: int = PANEL_MAX_OUTPUT) -> list[dict]:
    """Console text as the panel shows it: one {"text", "kind"} per line, kind "err", "warn", "ok" or ""; the
    terminal's "[Console]" prefixes and tick emoji dropped."""
    cut = len(text) > limit
    lines = []
    for raw in text[:limit].strip("\n").split("\n"):
        line, kind = raw.rstrip(), ""
        for prefix, replacement, prefix_kind in _PANEL_PREFIXES:
            if line.startswith(prefix):
                line, kind = replacement + line[len(prefix):], prefix_kind
                break
        if "✅ " in line:
            line, kind = line.replace("✅ ", ""), "ok"
        lines.append({"text": line, "kind": kind})
    if cut:
        lines.append({"text": f"(output cut at {limit:,} characters)", "kind": "warn"})
    return lines


class PanelConsole:
    """The operator panel's console. `shutdown` is the bot's request_shutdown."""

    reference = PANEL_COMMANDS  # (usage, description) pairs, for the panel's command list
    max_line = PANEL_MAX_LINE

    def __init__(self, bot: commands.Bot, shutdown, *, db_path: str | None = None,
                 timeout: float = PANEL_COMMAND_SECONDS):
        self.bot = bot
        self.shutdown = shutdown
        self.db_path = db_path or DB_PATH
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._stop_state = None  # None, "requested" (waiting for the reply to go out), then "scheduled"
        self._sql_proc = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def run(self, line: str) -> list[dict]:
        """One command's output as panel lines. A second command while one runs is turned away, not queued."""
        line = (line or "").strip()
        if not line:
            return []
        if self._lock.locked():
            return panel_lines("[Console Error] Another command is still running. Try again when it has finished.")
        async with self._lock:
            buffer = io.StringIO()
            token = _console_output.set(buffer)
            try:
                await self._dispatch(line)
            except asyncio.TimeoutError:
                print(f"[Console Error] Stopped waiting after {self.timeout:g} s. "
                      "The command may have done part of its work.")
            except Exception as exc:
                print(f"[Console Error] {exc}")
            finally:
                _console_output.reset(token)
        return panel_lines(buffer.getvalue())

    async def _dispatch(self, line: str):
        parts = line.split(None, 1)
        name, args = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if name == "help":
            width = max(len(usage) for usage, _ in PANEL_COMMANDS)
            print("The SSH console's commands, one at a time:")
            for usage, text in PANEL_COMMANDS:
                print(f"  {usage.ljust(width)}  {text}")
        elif name == "clear":
            print("[Console] Cleared.")  # the page clears its own output
        elif name in ("exit", "quit"):
            print("[Console] exit and quit leave a terminal prompt, and there's none here. To stop the bot, run stop.")
        elif name == "stop":
            self._stop(args)
        elif name == "sql":
            print(await self._sql(args), end="")
        else:
            await asyncio.wait_for(execute_console_command(self.bot, line), self.timeout)

    def _stop(self, args: str):
        if args.strip().lower() != "confirm":
            print("[Console Warning] This stops the bot and this website. Coolify starts them again, usually within "
                  "a minute, and the panel is unavailable until then.")
            print("[Console] To go ahead, run: stop confirm")
            return
        if self._stop_state is not None:
            print("[Console] Already stopping.")
            return
        log.warning("Operator panel console: stop confirmed")
        print("[Console] Stopping the bot. Reload this page in a minute or two.")
        # The website calls stop_after_reply once this reply has been sent, so the stop never cuts it off.
        self._stop_state = "requested"
        asyncio.get_running_loop().call_later(PANEL_STOP_FALLBACK, self.stop_after_reply)

    @property
    def stop_requested(self) -> bool:
        """`stop confirm` ran, and the bot stops once its reply is out (stop_after_reply)."""
        return self._stop_state == "requested"

    def stop_after_reply(self):
        """Stop the bot shortly, now that the reply to `stop confirm` has been sent. Only the first call counts."""
        if self._stop_state != "requested":
            return
        self._stop_state = "scheduled"
        asyncio.get_running_loop().call_later(PANEL_STOP_DELAY, self.shutdown)

    async def _sql(self, query: str) -> str:
        """The panel's read-only `sql` in its own process (panel_sql.py). The console stays busy until that process
        has exited: on a timeout, an error or a cancelled request it's killed first."""
        if not query.strip():
            return "[Console Error] Usage: sql <query>\n"
        request = json.dumps({"db": str(Path(self.db_path).resolve()), "query": query,
                              "seconds": PANEL_SQL_SECONDS, "max_rows": PANEL_SQL_MAX_ROWS}).encode("utf-8")
        env = {key: os.environ[key] for key in _SQL_WORKER_ENV if key in os.environ}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(_SQL_WORKER), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=env)
        except (OSError, NotImplementedError) as exc:
            return f"[Console SQL Error] Could not start the query: {exc}\n"
        self._sql_proc = proc
        out, overflow = b"", False
        try:
            proc.stdin.write(request)
            await proc.stdin.drain()
            proc.stdin.close()
            out, overflow = await asyncio.wait_for(_read_capped(proc.stdout, PANEL_SQL_MAX_BYTES),
                                                   PANEL_SQL_SECONDS + PANEL_SQL_GRACE)
            if not overflow:
                await asyncio.wait_for(proc.wait(), PANEL_SQL_GRACE)
        except asyncio.TimeoutError:
            return f"[Console SQL Error] Stopped after {PANEL_SQL_SECONDS:g} s, the panel's limit for one query.\n"
        except (BrokenPipeError, ConnectionResetError):
            out = b""
        finally:
            await _end_worker(proc, PANEL_SQL_GRACE)
            self._sql_proc = None
        if overflow:
            return "[Console SQL Error] The result was too large for the panel. Select fewer or narrower columns.\n"
        if proc.returncode != 0 or not out:
            return ("[Console SQL Error] The query stopped before it finished: it needed more time, memory or "
                    "temporary disk than the panel allows.\n")
        return out.decode("utf-8", "replace")

    def kill_worker(self):
        """Website shutdown: end a running `sql` process now."""
        proc = self._sql_proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    """(everything up to EOF, False), or (b"", True) as soon as more than `limit` bytes have come."""
    chunks, total = [], 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), False
        total += len(chunk)
        if total > limit:
            return b"", True
        chunks.append(chunk)


async def _end_worker(proc, seconds: float):
    """Kill the sql process if it's still running and reap it, all within `seconds`. Its output is drained
    meanwhile: asyncio reports the exit only once the pipe has closed, and unread output would keep it open."""
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    async def drain_and_wait():
        if proc.stdout is not None:
            while await proc.stdout.read(65536):
                pass
        await proc.wait()

    try:
        await asyncio.wait_for(drain_and_wait(), seconds)
    except asyncio.TimeoutError:
        log.warning("The panel's sql process didn't exit within %g s of being stopped.", seconds)
