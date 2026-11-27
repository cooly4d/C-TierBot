# Host Console Commands

When running the bot locally in your terminal (`python leaderboard_bot.py`), you can type commands directly into the terminal prompt to interact with the bot and manage servers without using Discord interactions.

## In a container (Coolify)

The container has no keyboard attached to the bot, so the bot also listens on `127.0.0.1:$CONSOLE_PORT` (the Docker image sets `8765`; off when unset). Open the app's **Terminal** tab in Coolify and run:

```sh
python console.py            # prompt: type commands as below
python console.py status     # run one command and exit
```

In the `console.py` prompt, `exit` / `quit` only leave the prompt and the bot keeps running. `stop` shuts the bot down, and Coolify restarts the container. `clear` clears your terminal. The port listens on loopback only, so only a shell inside the container can reach it.

## Website operator panel (`/admin`)

The panel needs one password. Its **Console** section runs the commands below too, one at a time, with two
differences: `sql` only reads (it can't change data, and `users.access_token` always reads as NULL), and `stop`
asks you to run `stop confirm` first. Change data over SSH with `python console.py`. Without JavaScript the console
still works: each command reloads the panel with its output, and reloading that page never runs it again.

The panel's `sql` reads the bot's own tables with SQLite's built-in functions, in a separate process limited to
2 seconds. It refuses other tables (a copy you made over SSH, a view), other functions, and `JOIN ... USING` or
`NATURAL JOIN` (write `JOIN ... ON a.column = b.column`). A column added to the bot's tables later reads as NULL
in the panel until it's added to `panel_sql.READABLE`.

To create the password, or to change it, on your own PC:

```sh
python tools/make_admin_password.py            # generates a 32-character password
python tools/make_admin_password.py --choose   # or type your own (16-128 characters)
```

Save the printed password in your password manager, then paste `WEB_ADMIN_PASSWORD_HASH` and `WEB_SESSION_SECRET`
into the app's environment variables in Coolify (with `WEB_PUBLIC_ORIGIN` set to the site's address) and redeploy.
Changing either value signs out every session. After 20 wrong passwords in an hour the login is closed for everyone
for the rest of that hour; a redeploy resets it. The SSH console above is never affected.

---

## Available Commands

| Command | Usage | Description | Example |
| :--- | :--- | :--- | :--- |
| **`help`** | `help` | Shows the list of available console commands. | `help` |
| **`send`** | `send <channel_id> <message>` | Sends a message to any Discord text channel. | `send 123456789012345678 Hello everyone!` |
| **`dm`** | `dm <user_id> <message>` | Sends a private direct message to a Discord user. | `dm 987654321098765432 Thanks for playing!` |
| **`reply`** | `reply <channel_id> <msg_id> <text>` | Replies to a specific message in a channel. | `reply 1234567890 987654321 Got it, looking into this.` |
| **`delete`** | `delete <channel_id> <msg_id>` | Deletes a message that the bot sent in a channel. | `delete 1234567890 987654321` |
| **`servers`** | `servers` *(or `guilds`)* | Lists all connected servers (Name, ID, Member Count). | `servers` |
| **`channels`** | `channels <guild_id>` | Lists all text channels in a specified server with their IDs. | `channels 123456789012345678` |
| **`status`** | `status` | Displays bot latency, uptime, and database metrics. | `status` |
| **`sql`** | `sql <query>` | Runs a query directly on `leaderboard.db` and renders results. One survev.de account can be linked to one Discord user; to free an account for someone else, remove the old link (that user runs `/verify` again to relink). | `sql SELECT discord_id, username FROM users LIMIT 5;`<br>`sql DELETE FROM users WHERE discord_id = 123456789012345678` |
| **`winrate`** | `winrate [guild_id]` | Shows NeatQueue Team 1 vs Team 2 winrate across match history. | `winrate` |
| **`queue`** | `queue <match_id> [guild_id]` | Inspects calculated stats and lookup diagnostics for a queue without posting to Discord. | `queue 4600` |
| **`sync`** | `sync` | Manually triggers slash command synchronization with Discord. | `sync` |
| **`clear`** | `clear` | Clears the terminal screen. | `clear` |
| **`exit`** | `exit` *(or `quit` / `stop`)* | Gracefully disconnects the bot and exits the process. | `exit` |

---

## Notes
* **Unique Channel IDs**: Discord channel IDs are globally unique across all servers. You never need to specify a server ID when using `send`, `reply`, or `delete`.
* **Non-blocking Execution**: The console listener runs asynchronously in a worker thread (`asyncio.to_thread`), meaning normal bot functions (slash commands, NeatQueue match monitoring, etc.) continue to run smoothly while the console waits for input.

