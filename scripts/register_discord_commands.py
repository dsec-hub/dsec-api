"""Register (or refresh) the DSEC games slash commands with Discord.

A one-off REST call (PUT overwrites the full global command set). Run after
changing a command's shape. Uses the bot token only to authenticate the REST
call — there is NO gateway socket.

Usage (from dsec-api/, with DISCORD_APPLICATION_ID + DISCORD_BOT_TOKEN set in
.env or the environment):

    .venv/bin/python -m scripts.register_discord_commands
    # scope a faster, guild-local refresh while developing:
    .venv/bin/python -m scripts.register_discord_commands --guild-id 123456789012345678

Global commands can take up to an hour to propagate; guild commands are instant.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402

# Option types (Discord application command option types).
STRING = 3

COMMANDS = [
    {
        "name": "codle",
        "description": "Play today's Codle (Wordle for code) right here in Discord.",
        "type": 1,  # CHAT_INPUT
        "options": [
            {
                "name": "guess",
                "description": "Your 5-letter guess (leave blank to see the board).",
                "type": STRING,
                "required": False,
            }
        ],
    },
    {
        "name": "leaderboard",
        "description": "Show the DSEC games leaderboard.",
        "type": 1,
        "options": [
            {
                "name": "game",
                "description": "Which game (default: overall).",
                "type": STRING,
                "required": False,
                "choices": [
                    {"name": "Codle", "value": "codle"},
                    {"name": "Flappy Duck", "value": "flappy-duck"},
                ],
            },
            {
                "name": "window",
                "description": "Time window (default: weekly).",
                "type": STRING,
                "required": False,
                "choices": [
                    {"name": "Daily", "value": "daily"},
                    {"name": "Weekly", "value": "weekly"},
                    {"name": "This month", "value": "cycle"},
                ],
            },
        ],
    },
    {
        "name": "link",
        "description": "Link your Discord to your DSEC account using a code from the portal.",
        "type": 1,
        "options": [
            {
                "name": "code",
                "description": "The link code from the member portal.",
                "type": STRING,
                "required": False,
            }
        ],
    },
    {
        "name": "play",
        "description": "Get the link to play Flappy Duck on the web.",
        "type": 1,
    },
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Register DSEC Discord slash commands.")
    ap.add_argument("--guild-id", default=None, help="register to one guild (instant) instead of global")
    args = ap.parse_args()

    app_id = settings.DISCORD_APPLICATION_ID
    token = settings.DISCORD_BOT_TOKEN
    if not app_id or not token:
        print("ERROR: set DISCORD_APPLICATION_ID and DISCORD_BOT_TOKEN first.")
        return 1

    if args.guild_id:
        url = f"https://discord.com/api/v10/applications/{app_id}/guilds/{args.guild_id}/commands"
    else:
        url = f"https://discord.com/api/v10/applications/{app_id}/commands"

    resp = httpx.put(
        url,
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
        json=COMMANDS,
        timeout=30,
    )
    if resp.status_code >= 300:
        print(f"ERROR {resp.status_code}: {resp.text}")
        return 1
    registered = [c["name"] for c in resp.json()]
    where = f"guild {args.guild_id}" if args.guild_id else "globally"
    print(f"Registered {len(registered)} commands {where}: {', '.join(registered)}")
    print("Set the Interactions Endpoint URL in the Developer Portal to:")
    print("  https://api.dsec.club/discord/interactions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
