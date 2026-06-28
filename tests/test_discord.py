"""Discord webhook bot tests — Ed25519 verification + interaction routing.

The signature path is exercised with a real generated keypair (the public key is
monkeypatched into settings). Routing is exercised in dev mode (blank public key
=> passthrough), matching how the other webhook tests reach their handlers.
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import models
from app.config import settings
from app.core.apikeys import generate_key


def _make_key(db, scopes, name="k"):
    gen = generate_key()
    db.add(models.APIKey(name=name, prefix=gen.prefix, key_hash=gen.key_hash, scopes=scopes))
    db.commit()
    return gen.raw_key


def _h(key):
    return {"Authorization": f"Bearer {key}"}


# --- Ed25519 signature verification -----------------------------------------


def test_discord_ed25519_signature_is_verified(client, monkeypatch):
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()
    monkeypatch.setattr(settings, "DISCORD_PUBLIC_KEY", pub_hex)

    body = json.dumps({"type": 1}).encode()
    timestamp = "1700000000"
    sig = priv.sign(timestamp.encode() + body).hex()

    ok = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    assert ok.status_code == 200
    assert ok.json() == {"type": 1}  # PONG

    # A tampered body fails the signature (401), never reaching the handler.
    bad = client.post(
        "/discord/interactions",
        content=json.dumps({"type": 1, "x": "tampered"}).encode(),
        headers={
            "X-Signature-Ed25519": sig,
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )
    assert bad.status_code == 401

    # Missing signature headers => 401.
    missing = client.post("/discord/interactions", content=body, headers={"Content-Type": "application/json"})
    assert missing.status_code == 401


# --- interaction routing (dev passthrough: blank public key) -----------------


def test_ping_returns_pong(client):
    r = client.post("/discord/interactions", json={"type": 1})
    assert r.status_code == 200 and r.json() == {"type": 1}


def test_play_returns_deep_link(client):
    r = client.post(
        "/discord/interactions",
        json={"type": 2, "data": {"name": "play"}, "member": {"user": {"id": "1"}}},
    )
    data = r.json()["data"]
    assert "flappy-duck" in data["content"]
    assert data["flags"] == 64  # ephemeral


def test_codle_requires_link_then_plays(client, db):
    # Unlinked Discord user is told to link first.
    unlinked = client.post(
        "/discord/interactions",
        json={"type": 2, "data": {"name": "codle"}, "member": {"user": {"id": "555"}}},
    ).json()
    assert "link" in unlinked["data"]["content"].lower()

    # Link the user to an account, then a guess returns a board embed.
    db.add(models.GamePlayer(account_id=777, email="d@uni.edu", display_name="Ducky"))
    db.commit()
    player = db.query(models.GamePlayer).filter_by(account_id=777).first()
    db.add(models.GameAccountLink(player_id=player.id, discord_user_id="555"))
    db.commit()

    played = client.post(
        "/discord/interactions",
        json={
            "type": 2,
            "data": {"name": "codle", "options": [{"name": "guess", "value": "PRINT"}]},
            "member": {"user": {"id": "555", "username": "ducky"}},
        },
    ).json()
    embed = played["data"]["embeds"][0]
    assert embed["title"] == "Codle"
    assert "`P R I N T`" in embed["description"]


def test_leaderboard_command(client, db):
    r = client.post(
        "/discord/interactions",
        json={"type": 2, "data": {"name": "leaderboard", "options": [{"name": "window", "value": "weekly"}]}},
    )
    embed = r.json()["data"]["embeds"][0]
    assert "Leaderboard" in embed["title"]


def test_link_command_claims_code(client, db):
    rw = _make_key(db, ["read", "write"], "rw")
    started = client.post(
        "/game-link/start", json={"account_id": 888, "email": "z@uni.edu"}, headers=_h(rw)
    ).json()
    code = started["code"]
    claimed = client.post(
        "/discord/interactions",
        json={
            "type": 2,
            "data": {"name": "link", "options": [{"name": "code", "value": code}]},
            "member": {"user": {"id": "31337"}},
        },
    ).json()
    assert "Linked" in claimed["data"]["content"]
    # And now that Discord user resolves to the account.
    from app.features.game_link import service as link_service

    assert link_service.get_player_by_discord(db, "31337").account_id == 888
