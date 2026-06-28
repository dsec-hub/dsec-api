# Games platform (API engine)

The brain for the DSEC games. Clients (the `dsec-games` site, the Discord bot)
render and submit; this engine decides every score, every point, every
leaderboard position and the monthly draw. A client never computes points or
decides a winner.

## Layout

```
games/
  engines/
    base.py          GameEngine ABC — the extensibility seam
    codle.py         Codle (Wordle for code), server-authoritative
    flappy_duck.py   Flappy Duck (arcade), client-scored + mitigated
    wordlist.py      Codle answer pool
    __init__.py      REGISTRY {slug: engine}  <- add a game here
  scoring.py         raw_score -> points, clamped to the per-game daily cap
  sessions.py        signed short-lived Flappy play sessions (anti-replay)
  draws.py           monthly roll-up; highest member points wins (not random)
  service.py         rounds, the single submit path, leaderboards, /me
  schemas.py         request/response models
  router.py          REST routes (mounted at /games)
game_link/           player identity + Discord<->account link codes (/game-link)
```

## Adding a game later

It is meant to be trivial: drop a new `engines/<slug>.py` implementing
`GameEngine`, then add one line to `engines/__init__.py` REGISTRY. The service,
router, leaderboard, draw and resume-state path are all engine-agnostic — there
are NO game-name branches in them. Behaviour is driven by engine flags, not slugs:

- `single_attempt_per_round` — one continued attempt per round (Codle) vs a fresh
  attempt per play (Flappy).
- `nonmember_round_play_cap` — how many plays a non-member gets per round.
- `points_per_day_cap` — the cross-game daily points ceiling.
- `requires_session` — client-scored games get a signed play session issued on
  `round` and verified on `attempt` (the anti-replay / wall-clock check), with no
  per-game code in the service.

A stateful game's board resumes through the generic `game_state()` (it returns the
engine's own client-safe attempt `detail`), so even a new Codle-like game needs no
service/router edits.

## One ledger, two surfaces

Every play (Discord or portal) writes one `game_attempt` row keyed to the student
account (`game_player`, whose `account_id` is the dsec-app `portal_account.id`).
Both surfaces share the same daily `game_round`, so a Codle played in Discord and
a Codle played in the portal on the same day are the same puzzle and roll into
the same monthly points total.

## Membership gating

Membership is resolved against the read-only DUSA members roster (the weekly
import is the oracle), never a second notion of member.

- Members (`is_current`): unlimited plays; all plays earn points and count toward
  the draw.
- Non-members: one play per game per day (the taste/hook). They still appear on
  the public leaderboards, but the gift-card **draw counts only member plays**
  (`is_member_play`). The draw is highest-total-points, a skill competition — not
  a random lottery.

## Anti-cheat

- **Codle** is server-authoritative: the server holds the answer (in
  `game_round.payload`, never returned to a client) and validates each guess.
- **Flappy Duck** computes its score on the client, so it is inherently
  spoofable. We mitigate, we do not pretend to prevent: a signed short-lived play
  session binds the score to a server-issued round and bounds replay/timing, the
  submission carries a gameplay digest the engine sanity-checks (score-per-second
  ceiling, run length, input count), and points are capped per day so a spoofed
  score cannot dominate the points-based draw.

## Privacy (read before go-live)

This feature binds student account ids to gameplay activity (`game_player`,
`game_attempt`, `game_account_link`). That data use must sit inside the
DUSA-cleared privacy posture for the member portal before go-live. Keep attempt
`detail` free of anything sensitive — it currently holds only board state (Codle
guesses/marks) and run stats (Flappy score/duration/flaps), no PII. Email is
stored on `game_player` only as the membership-resolution key and is never
returned on a leaderboard (only `display_name`).
