"""Games platform tests — the API is the only brain.

Covers: engine validate_attempt (Codle marking + Flappy anti-cheat), per-game
daily points capping, member vs non-member play limits, leaderboard window
queries, the monthly draw roll-up (highest member points wins), and a
cross-surface proof that a Discord play and a portal play share one round + one
ledger. Engine/scoring logic is exercised directly; auth/HTTP via the client.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import models
from app.core.apikeys import generate_key
from app.features.games import draws, service, sessions
from app.features.games.engines import get_engine
from app.features.games.engines.codle import _score_guess


# --- local auth helpers (per the conftest convention) ------------------------


def _make_key(db, scopes, name="k"):
    gen = generate_key()
    db.add(models.APIKey(name=name, prefix=gen.prefix, key_hash=gen.key_hash, scopes=scopes))
    db.commit()
    return gen.raw_key


@pytest.fixture
def rw_key(db):
    return _make_key(db, ["read", "write"], "rw")


@pytest.fixture
def ro_key(db):
    return _make_key(db, ["read"], "ro")


def _h(key):
    return {"Authorization": f"Bearer {key}"}


def _add_member(db, *, account_email, name="Member"):
    db.add(
        models.Member(
            student_id=f"S-{account_email}",
            full_name=name,
            email=account_email,
            is_current=True,
            dusa_member=True,
            membership_type="New",
        )
    )
    db.commit()


def _codle_answer(db):
    service.get_game(db, "codle")  # ensure round exists
    g = db.execute(select(models.Game).where(models.Game.slug == "codle")).scalars().first()
    service.get_or_create_today_round(db, g, get_engine("codle"), datetime.now(timezone.utc))
    rnd = db.execute(select(models.GameRound).where(models.GameRound.game_id == g.id)).scalars().first()
    return rnd.payload["answer"]


def _flappy_session(db, *, seconds_ago=120):
    """A signed Flappy session backdated so a real run fits inside elapsed time."""
    g = service.get_game(db, "flappy-duck")
    service.get_or_create_today_round(db, g, get_engine("flappy-duck"), datetime.now(timezone.utc))
    rnd = db.execute(select(models.GameRound).where(models.GameRound.game_id == g.id)).scalars().first()
    seed = rnd.payload["seed"]
    now = int(datetime.now(timezone.utc).timestamp())
    return sessions.sign_session(seed=seed, issued_at_epoch=now - seconds_ago)


# --- engine: Codle marking ---------------------------------------------------


def test_codle_marking_handles_duplicates():
    # exact + present + duplicate-letter bounding
    assert _score_guess("TYPES", "BYTES") == ["present", "correct", "absent", "correct", "correct"]
    assert _score_guess("ARRAY", "RAISE") == ["present", "present", "absent", "absent", "absent"]
    # a fully correct guess
    assert _score_guess("STACK", "STACK") == ["correct"] * 5


def test_codle_round_is_deterministic_and_hides_answer():
    eng = get_engine("codle")
    a = eng.generate_round("2026-06-24")
    b = eng.generate_round("2026-06-24")
    assert a == b  # same day -> same puzzle (cross-surface fairness)
    assert "answer" not in eng.public_round(a)


def test_codle_rejects_bad_guess():
    eng = get_engine("codle")
    payload = eng.generate_round("2026-06-24")
    with pytest.raises(ValueError):
        eng.validate_attempt(payload, {"guess": "AB"})  # wrong length
    with pytest.raises(ValueError):
        eng.validate_attempt(payload, {"guess": "12345"})  # not alpha


# --- engine: Flappy anti-cheat ----------------------------------------------


def test_flappy_rejects_impossible_runs():
    eng = get_engine("flappy-duck")
    r = eng.generate_round("2026-06-24")
    # legit
    ok = eng.validate_attempt(r, {"score": 12, "duration_ms": 20000, "flaps": 40})
    assert ok["raw_score"] == 12.0
    # too fast for the score
    with pytest.raises(ValueError):
        eng.validate_attempt(r, {"score": 500, "duration_ms": 1000, "flaps": 600})
    # too few inputs
    with pytest.raises(ValueError):
        eng.validate_attempt(r, {"score": 50, "duration_ms": 60000, "flaps": 3})
    # over the absolute ceiling
    with pytest.raises(ValueError):
        eng.validate_attempt(r, {"score": 99999, "duration_ms": 9_000_000, "flaps": 99999})


# --- points capping ----------------------------------------------------------


def test_points_capped_per_game_per_day(db):
    _add_member(db, account_email="cap@uni.edu")
    total = 0
    for score, dur, flaps in [(10, 20000, 30), (40, 50000, 80), (30, 40000, 60)]:
        sess = _flappy_session(db)
        res = service.submit_attempt(
            db,
            slug="flappy-duck",
            account_id=1,
            email="cap@uni.edu",
            display_name="Capper",
            submission={"session": sess, "score": score, "duration_ms": dur, "flaps": flaps},
            surface="portal",
        )
        total += res["points"]
    # Flappy cap is 50/day: 10 + 40 + (clamped 0) = 50, never more.
    assert total == 50
    cap = get_engine("flappy-duck").points_per_day_cap
    assert total == cap


# --- member vs non-member play limits ---------------------------------------


def test_nonmember_gets_one_flappy_play_per_day(db):
    # account 2 has no member row -> non-member
    sess = _flappy_session(db)
    first = service.submit_attempt(
        db,
        slug="flappy-duck",
        account_id=2,
        email="guest@uni.edu",
        submission={"session": sess, "score": 8, "duration_ms": 16000, "flaps": 20},
        surface="portal",
    )
    assert first["is_member_play"] is False
    with pytest.raises(service.GameError) as exc:
        service.submit_attempt(
            db,
            slug="flappy-duck",
            account_id=2,
            email="guest@uni.edu",
            submission={"session": _flappy_session(db), "score": 5, "duration_ms": 12000, "flaps": 15},
            surface="portal",
        )
    assert exc.value.status_code == 403


def test_member_can_replay_flappy(db):
    _add_member(db, account_email="m@uni.edu")
    for _ in range(3):
        res = service.submit_attempt(
            db,
            slug="flappy-duck",
            account_id=3,
            email="m@uni.edu",
            submission={"session": _flappy_session(db), "score": 5, "duration_ms": 12000, "flaps": 15},
            surface="portal",
        )
        assert res["is_member_play"] is True  # all three accepted


def test_codle_is_one_attempt_per_round(db):
    answer = _codle_answer(db)
    service.submit_attempt(db, slug="codle", account_id=4, email="c@uni.edu", submission={"guess": "PRINT"})
    service.submit_attempt(db, slug="codle", account_id=4, email="c@uni.edu", submission={"guess": answer})
    with pytest.raises(service.GameError) as exc:
        service.submit_attempt(db, slug="codle", account_id=4, email="c@uni.edu", submission={"guess": answer})
    assert exc.value.status_code == 409  # already played today


# --- leaderboard windows -----------------------------------------------------


def test_leaderboard_windows_rank_by_points(db):
    _add_member(db, account_email="a@uni.edu", name="Ada")
    _add_member(db, account_email="b@uni.edu", name="Bob")
    a_ans = _codle_answer(db)
    # Ada solves in 1 (60 pts); Bob solves in 2 (50 pts)
    service.submit_attempt(db, slug="codle", account_id=10, email="a@uni.edu", display_name="Ada", submission={"guess": a_ans})
    service.submit_attempt(db, slug="codle", account_id=11, email="b@uni.edu", display_name="Bob", submission={"guess": "PRINT"})
    service.submit_attempt(db, slug="codle", account_id=11, email="b@uni.edu", submission={"guess": a_ans})
    for window in ("daily", "weekly", "cycle"):
        board = service.leaderboard(db, window=window)
        assert [e["display_name"] for e in board] == ["Ada", "Bob"]
        assert board[0]["points"] == 60 and board[1]["points"] == 50
        assert board[0]["rank"] == 1 and board[1]["rank"] == 2


# --- the monthly draw --------------------------------------------------------


def test_draw_roll_up_picks_highest_member_points(db):
    _add_member(db, account_email="a@uni.edu", name="Ada")
    _add_member(db, account_email="b@uni.edu", name="Bob")
    a_ans = _codle_answer(db)
    # Ada 60, Bob 50, plus a non-member with a big leaderboard score that must NOT win the draw
    service.submit_attempt(db, slug="codle", account_id=20, email="a@uni.edu", display_name="Ada", submission={"guess": a_ans})
    service.submit_attempt(db, slug="codle", account_id=21, email="b@uni.edu", display_name="Bob", submission={"guess": "PRINT"})
    service.submit_attempt(db, slug="codle", account_id=21, email="b@uni.edu", submission={"guess": a_ans})
    # non-member flappy (counts on leaderboard, NOT in the draw)
    service.submit_attempt(
        db, slug="flappy-duck", account_id=22, email="guest@uni.edu", display_name="Guest",
        submission={"session": _flappy_session(db), "score": 40, "duration_ms": 60000, "flaps": 90}, surface="portal",
    )
    period = draws.cycle_key(datetime.now(timezone.utc))
    rolled = draws.roll_up_cycle(db, period)
    assert rolled["winner"]["display_name"] == "Ada"  # highest MEMBER points
    # the non-member is absent from the members-only draw standings
    assert "Guest" not in [s["display_name"] for s in rolled["standings"]]

    cycle = draws.close_cycle(db, period)
    assert cycle.status == "closed"
    winner_player = db.get(models.GamePlayer, cycle.winner_player_id)
    assert winner_player.account_id == 20  # Ada
    # idempotent
    again = draws.close_cycle(db, period)
    assert again.winner_player_id == cycle.winner_player_id


# --- cross-surface: one round, one ledger ------------------------------------


def test_discord_and_portal_share_one_round(db):
    """A Codle played in the portal and one played 'in Discord' on the same day
    write to the SAME round and roll into the same monthly total."""
    _add_member(db, account_email="a@uni.edu", name="Ada")
    answer = _codle_answer(db)
    # portal play (guess 1) then a Discord play (guess 2) by the same account
    service.submit_attempt(db, slug="codle", account_id=30, email="a@uni.edu", display_name="Ada", submission={"guess": "PRINT"}, surface="portal")
    res = service.submit_attempt(db, slug="codle", account_id=30, email="a@uni.edu", submission={"guess": answer}, surface="discord")
    assert res["solved"] is True

    # exactly one codle round today, and the single attempt carries that round_id
    g = db.execute(select(models.Game).where(models.Game.slug == "codle")).scalars().first()
    rounds = db.execute(select(models.GameRound).where(models.GameRound.game_id == g.id)).scalars().all()
    assert len(rounds) == 1
    attempts = db.execute(select(models.GameAttempt).where(models.GameAttempt.game_id == g.id)).scalars().all()
    assert len(attempts) == 1  # continued, not duplicated
    assert attempts[0].round_id == rounds[0].id


def test_two_accounts_get_the_same_daily_round(db):
    answer = _codle_answer(db)
    service.submit_attempt(db, slug="codle", account_id=40, email="x@uni.edu", submission={"guess": "PRINT"})
    service.submit_attempt(db, slug="codle", account_id=41, email="y@uni.edu", submission={"guess": "PRINT"})
    g = db.execute(select(models.Game).where(models.Game.slug == "codle")).scalars().first()
    rounds = db.execute(select(models.GameRound).where(models.GameRound.game_id == g.id)).scalars().all()
    assert len(rounds) == 1  # both players, one shared puzzle


# --- HTTP surface: auth, scopes, no answer leak ------------------------------


def test_round_endpoint_never_leaks_answer(client, ro_key):
    r = client.get("/games/codle/round", headers=_h(ro_key))
    assert r.status_code == 200
    body = r.json()
    assert "answer" not in body
    assert body["length"] == 5 and body["max_guesses"] == 6


def test_state_endpoint_resumes_board(client, rw_key, ro_key, db):
    # Generic /{slug}/state: empty before any play, then resumes the board after a
    # guess (proves the engine-agnostic resume path, no per-game router branch).
    before = client.get("/games/codle/state?account_id=51", headers=_h(ro_key)).json()
    assert before["started"] is False and "answer" not in before
    client.post(
        "/games/codle/attempt",
        json={"account_id": 51, "email": "s@uni.edu", "submission": {"guess": "PRINT"}},
        headers=_h(rw_key),
    )
    after = client.get("/games/codle/state?account_id=51", headers=_h(ro_key)).json()
    assert after["started"] is True
    assert after["guesses"][0]["guess"] == "PRINT"
    assert after["finished"] is False
    assert "answer" not in after  # not revealed mid-game


def test_attempt_requires_write_scope(client, ro_key, rw_key):
    body = {"account_id": 50, "email": "h@uni.edu", "submission": {"guess": "PRINT"}}
    assert client.post("/games/codle/attempt", json=body, headers=_h(ro_key)).status_code == 403
    assert client.post("/games/codle/attempt", json=body).status_code == 401
    ok = client.post("/games/codle/attempt", json=body, headers=_h(rw_key))
    assert ok.status_code == 200
    assert ok.json()["finished"] is False  # one guess, not solved


def test_cron_close_draw_route(client, rw_key, db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "CRON_SECRET", "test-cron")
    _add_member(db, account_email="w@uni.edu", name="Winner")
    ans = _codle_answer(db)
    client.post(
        "/games/codle/attempt",
        json={"account_id": 70, "email": "w@uni.edu", "display_name": "Winner", "submission": {"guess": ans}},
        headers=_h(rw_key),
    )
    period = draws.cycle_key(datetime.now(timezone.utc))
    # Wrong / missing secret is rejected (Vercel Cron sends Authorization: Bearer).
    assert client.get(f"/games/cron/close-draw?period_key={period}").status_code == 401
    # The real cron call closes the cycle and names the winner.
    r = client.get(
        f"/games/cron/close-draw?period_key={period}",
        headers={"Authorization": "Bearer test-cron"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "closed"
    assert body["winner_player_id"] is not None


def test_list_games_and_leaderboard_endpoints(client, ro_key, rw_key):
    games = client.get("/games", headers=_h(ro_key)).json()
    assert {g["slug"] for g in games} == {"codle", "flappy-duck"}
    lb = client.get("/games/leaderboard?window=daily", headers=_h(ro_key)).json()
    assert lb["window"] == "daily" and lb["entries"] == []


def test_link_flow_binds_discord_to_account(client, rw_key, ro_key, db):
    started = client.post(
        "/game-link/start", json={"account_id": 60, "email": "link@uni.edu", "display_name": "Linker"}, headers=_h(rw_key)
    ).json()
    code = started["code"]
    assert code.startswith("DUCK-")
    claimed = client.post(
        "/game-link/claim", json={"discord_user_id": "99887766", "code": code}, headers=_h(rw_key)
    )
    assert claimed.status_code == 200 and claimed.json()["account_id"] == 60
    status = client.get("/game-link/status?discord_user_id=99887766", headers=_h(ro_key)).json()
    assert status["linked"] is True and status["account_id"] == 60
    # a bogus code claims nothing
    assert client.post("/game-link/claim", json={"discord_user_id": "1", "code": "DUCK-0000-0000"}, headers=_h(rw_key)).status_code == 404
