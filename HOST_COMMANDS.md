# Host Console Commands

When running the bot locally in your terminal (`python leaderboard_bot.py`), you can type commands directly into the terminal prompt to interact with the bot and manage servers without using Discord interactions.

---

## Available Commands

| Command | Usage | Description | Example |
| :--- | :--- | :--- | :--- |
| **`help`** | `help` | Shows the list of available console commands. | `help` |
| **`send`** | `send <channel_id> <message>` | Sends a message to any Discord text channel. | `send 123456789012345678 Hello everyone!` |
| **`dm`** | `dm <user_id> <message>` | Sends a private direct message to a Discord user. | `dm 987654321098765432 Thanks for playing!` |
| **`reply`** | `reply <channel_id> <msg_id> <text>` | Replies to a specific message in a channel. | `reply 1234567890 987654321 Got it, looking into this.` |
| **`servers`** | `servers` *(or `guilds`)* | Lists all connected servers (Name, ID, Member Count). | `servers` |
| **`channels`** | `channels <guild_id>` | Lists all text channels in a specified server with their IDs. | `channels 123456789012345678` |
| **`status`** | `status` | Displays bot latency, uptime, and database metrics. | `status` |
| **`sql`** | `sql <query>` | Runs a query directly on `leaderboard.db` and renders results. | `sql SELECT discord_id, username FROM users LIMIT 5;` |
| **`sync`** | `sync` | Manually triggers slash command synchronization with Discord. | `sync` |
| **`clear`** | `clear` | Clears the terminal screen. | `clear` |
| **`exit`** | `exit` *(or `quit` / `stop`)* | Gracefully disconnects the bot and exits the process. | `exit` |

---

## Notes
* **Unique Channel IDs**: Discord channel IDs are globally unique across all servers. You never need to specify a server ID when using `send` or `reply`.
* **Non-blocking Execution**: The console listener runs asynchronously in a worker thread (`asyncio.to_thread`), meaning normal bot functions (slash commands, NeatQueue match monitoring, etc.) continue to run smoothly while the console waits for input.

