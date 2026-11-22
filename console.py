"""Host console client: run console commands against the running bot.

Inside the container the bot has no keyboard attached, so it listens on 127.0.0.1:CONSOLE_PORT instead
(see start_console_socket_server in console_service.py). Open a terminal in the container and run:

    python console.py            interactive prompt, same commands as the host console ('help')
    python console.py status     run one command and exit

In the prompt, 'exit' or 'quit' leaves the prompt and leaves the bot running. 'stop' shuts the bot down,
and Coolify then restarts the container.
"""
import os
import socket
import sys

DEFAULT_PORT = 8765
TIMEOUT_SECONDS = 180  # queue and winrate lookups call NeatQueue and survev.de, which can take a while


def send_command(line: str, port: int) -> str:
    with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT_SECONDS) as conn:
        conn.sendall(line.strip().encode("utf-8") + b"\n")
        chunks = []
        while True:
            data = conn.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_one(line: str, port: int) -> bool:
    try:
        output = send_command(line, port)
    except ConnectionRefusedError:
        print(f"[console.py] Nothing is listening on 127.0.0.1:{port}. Is the bot running and ready?")
        return False
    except OSError as exc:
        print(f"[console.py] Could not reach the bot: {exc}")
        return False
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return True


def main(argv: list[str]) -> int:
    port = int(os.getenv("CONSOLE_PORT") or DEFAULT_PORT)
    if argv:
        return 0 if run_one(" ".join(argv), port) else 1

    print("C-TierBot console. Type 'help' for commands, 'exit' to leave (the bot keeps running).")
    while True:
        try:
            line = input("c-tierbot> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        command = line.split(None, 1)[0].lower()
        if command in ("exit", "quit"):
            return 0
        if command == "clear":
            os.system("cls" if os.name == "nt" else "clear")
            continue
        run_one(line, port)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
