"""Discord interaction handling — the webhook bot's logic.

This is a THIN client: it calls the games brain (`games.service`,
`game_link.service`) in-process and shapes the result into a Discord interaction
response. It never computes a score or a point itself. No gateway socket — Discord
POSTs each interaction to /discord/interactions and we answer inline (in-process
DB work is well under the 3s deadline, so no deferral needed).

Interaction types we handle:
  1 PING                 -> PONG
  2 APPLICATION_COMMAND  -> /codle /leaderboard /link /play
  3 MESSAGE_COMPONENT    -> the Codle "play again" hint button (best-effort)
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.features.game_link import service as link_service
from app.features.games import service as games_service

# Discord interaction + response type/flag constants.
PING = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3
PONG = 1
CHANNEL_MESSAGE = 4  # CHANNEL_MESSAGE_WITH_SOURCE
EPHEMERAL = 64

_MARK_EMOJI = {"correct": "🟩", "present": "🟨", "absent": "⬛"}


# --- response helpers --------------------------------------------------------


def _ephemeral(content: str) -> dict:
    return {"type": CHANNEL_MESSAGE, "data": {"content": content, "flags": EPHEMERAL}}


def _embed(embed: dict, *, ephemeral: bool = True) -> dict:
    data: dict = {"embeds": [embed]}
    if ephemeral:
        data["flags"] = EPHEMERAL
    return {"type": CHANNEL_MESSAGE, "data": data}


# --- interaction parsing -----------------------------------------------------


def _user_id(interaction: dict) -> str | None:
    member = interaction.get("member") or {}
    user = member.get("user") or interaction.get("user") or {}
    return user.get("id")


def _display_name(interaction: dict) -> str | None:
    member = interaction.get("member") or {}
    user = member.get("user") or interaction.get("user") or {}
    return member.get("nick") or user.get("global_name") or user.get("username")


def _options(interaction: dict) -> dict:
    data = interaction.get("data") or {}
    return {o["name"]: o.get("value") for o in data.get("options", []) if "name" in o}


# --- board rendering ---------------------------------------------------------


def _render_board(guesses: list[dict]) -> str:
    if not guesses:
        return "_No guesses yet._"
    lines = []
    for g in guesses:
        marks = "".join(_MARK_EMOJI.get(m, "⬛") for m in g.get("marks", []))
        letters = " ".join(g.get("guess", ""))
        lines.append(f"{marks}  `{letters}`")
    return "\n".join(lines)


def _codle_embed(state: dict, *, footer: str | None = None) -> dict:
    board = _render_board(state.get("guesses", []))
    if state.get("finished"):
        if state.get("solved"):
            title = "Codle — solved!"
            desc = f"{board}\n\nNice work. Worth **{state.get('points', 0)}** points."
        else:
            title = "Codle — out of guesses"
            desc = f"{board}\n\nThe word was **{state.get('answer', '?????')}**. Back tomorrow."
    else:
        used = state.get("guesses_used", 0)
        total = state.get("max_guesses", 6)
        title = "Codle"
        desc = f"{board}\n\nGuess {used}/{total}. Reply with `/codle guess:<word>`."
    embed = {"title": title, "description": desc, "color": 0xE91E63}
    if footer:
        embed["footer"] = {"text": footer}
    return embed


# --- command handlers --------------------------------------------------------


def _resolve_account(db: Session, interaction: dict) -> int | None:
    discord_id = _user_id(interaction)
    if not discord_id:
        return None
    player = link_service.get_player_by_discord(db, discord_id)
    return player.account_id if player else None


def _handle_codle(db: Session, interaction: dict) -> dict:
    account_id = _resolve_account(db, interaction)
    if account_id is None:
        return _ephemeral(
            "Link your DSEC account first: open the member portal, copy your link "
            "code, then run `/link <code>` here."
        )
    guess = _options(interaction).get("guess")
    if not guess:
        state = games_service.codle_state(db, account_id=account_id)
        if not state.get("started"):
            return _embed(
                {
                    "title": "Codle",
                    "description": (
                        f"Today's word is {state.get('length', 5)} letters, "
                        f"{state.get('max_guesses', 6)} guesses.\n\n"
                        "Reply with `/codle guess:<word>` to play."
                    ),
                    "color": 0xE91E63,
                }
            )
        return _embed(_codle_embed(state))
    try:
        result = games_service.submit_attempt(
            db,
            slug="codle",
            account_id=account_id,
            display_name=_display_name(interaction),
            submission={"guess": str(guess)},
            surface="discord",
        )
    except games_service.GameError as exc:
        return _ephemeral(exc.message)
    # Re-read the full board state for a tidy render.
    state = games_service.codle_state(db, account_id=account_id)
    footer = None
    if result.get("finished") and result.get("leaderboard_position"):
        footer = f"Monthly rank: #{result['leaderboard_position']}"
    return _embed(_codle_embed(state, footer=footer))


def _handle_leaderboard(db: Session, interaction: dict) -> dict:
    opts = _options(interaction)
    game = opts.get("game")  # slug or None for overall
    window = opts.get("window") or "weekly"
    if window not in ("daily", "weekly", "cycle"):
        window = "weekly"
    entries = games_service.leaderboard(db, game_slug=game, window=window, limit=10)
    if not entries:
        return _embed(
            {"title": "Leaderboard", "description": "No points yet. Be the first.", "color": 0xFFCF33}
        )
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = []
    for e in entries:
        badge = medals.get(e["rank"], f"{e['rank']}.")
        lines.append(f"{badge} {e['display_name']} — **{e['points']}**")
    scope = (game or "overall").replace("-", " ")
    return _embed(
        {
            "title": f"Leaderboard — {scope} ({window})",
            "description": "\n".join(lines),
            "color": 0xFFCF33,
        },
        ephemeral=False,
    )


def _handle_link(db: Session, interaction: dict) -> dict:
    code = _options(interaction).get("code")
    if not code:
        return _ephemeral(
            "Open the member portal, copy your games link code, then run "
            f"`/link <code>`.\n{settings.GAMES_BASE_URL}"
        )
    discord_id = _user_id(interaction)
    if not discord_id:
        return _ephemeral("Could not read your Discord id. Try again.")
    player = link_service.link_discord(db, discord_user_id=discord_id, code=str(code))
    if player is None:
        return _ephemeral("That code did not match an account. Grab a fresh one in the portal.")
    return _ephemeral(
        "Linked. Your Discord is now connected to your DSEC games account — play with `/codle`."
    )


def _handle_play(interaction: dict) -> dict:
    return _ephemeral(f"Flap the duck here: {settings.GAMES_BASE_URL}/flappy-duck")


_COMMANDS = {
    "codle": lambda db, i: _handle_codle(db, i),
    "leaderboard": lambda db, i: _handle_leaderboard(db, i),
    "link": lambda db, i: _handle_link(db, i),
    "play": lambda db, i: _handle_play(i),
}


def handle_interaction(db: Session, interaction: dict) -> dict:
    itype = interaction.get("type")
    if itype == PING:
        return {"type": PONG}
    if itype == APPLICATION_COMMAND:
        name = (interaction.get("data") or {}).get("name", "")
        handler = _COMMANDS.get(name)
        if handler is None:
            return _ephemeral("Unknown command.")
        return handler(db, interaction)
    if itype == MESSAGE_COMPONENT:
        # The only component we ship is an informational hint; ack it quietly.
        return _ephemeral("Run `/codle guess:<word>` to play.")
    return _ephemeral("Unsupported interaction.")
