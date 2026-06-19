"""Member verification cards: the authed code/QR endpoint + the public verify.

The code is HMAC-derived from the roster id, so these don't touch the network
and stay deterministic across runs (AGENT_SECRET is pinned in conftest)."""

from __future__ import annotations

import pytest

from app import models
from app.core.apikeys import generate_key
from app.features.members import verification


@pytest.fixture
def ro_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    return gen.raw_key


def _h(key):
    return {"Authorization": f"Bearer {key}"}


def test_member_code_is_stable_unique_and_formatted():
    a, a2, b = verification.member_code(1), verification.member_code(1), verification.member_code(2)
    assert a == a2 and a != b
    assert a.startswith("DSEC-") and len(a) == len("DSEC-XXXX-XXXX")


def test_normalize_folds_lookalikes_and_separators():
    code = verification.member_code(7)
    core = verification.normalize_code(code)
    assert verification.normalize_code(code.lower()) == core
    assert verification.normalize_code(code.replace("-", " ")) == core
    # O/I/L look-alikes a human might type fold back to 0/1/1.
    assert verification.normalize_code(core.replace("0", "O").replace("1", "I")) == core


def test_verification_code_endpoint_returns_code_and_qr(client, ro_key, db):
    db.add(models.Member(student_id="S1", full_name="Ada Lovelace", is_current=True,
                         dusa_member=True, membership_type="New"))
    db.commit()
    member = client.get("/members", headers=_h(ro_key)).json()[0]
    r = client.get(f"/members/{member['id']}/verification-code", headers=_h(ro_key))
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == verification.member_code(member["id"])
    assert body["full_name"] == "Ada Lovelace"
    assert body["verify_url"].endswith(body["code"])
    assert body["qr_svg"] and body["qr_svg"].lstrip().startswith("<svg")


def test_verification_code_requires_key(client, db):
    db.add(models.Member(student_id="S9", full_name="No Key", is_current=True))
    db.commit()
    assert client.get("/members/1/verification-code").status_code == 401


def test_public_verify_resolves_code_to_member_without_auth(client, ro_key, db):
    db.add(models.Member(student_id="S2", full_name="Grace Hopper", is_current=True,
                         dusa_member=True, membership_type="Renewal"))
    db.commit()
    member = client.get("/members", headers=_h(ro_key)).json()[0]
    code = verification.member_code(member["id"])

    res = client.get(f"/members/verify/{code}").json()  # NO auth header
    assert res["valid"] is True
    assert res["member_id"] == member["id"]
    assert res["full_name"] == "Grace Hopper"
    # Public result must not leak contact identifiers.
    assert "email" not in res and "student_id" not in res


def test_public_verify_rejects_unknown_and_non_current(client, ro_key, db):
    # An unknown code → not valid.
    assert client.get("/members/verify/DSEC-0000-0000").json()["valid"] is False
    assert client.get("/members/verify/garbage").json()["valid"] is False

    # A member who has fallen off the roster (is_current=False) is not verifiable.
    db.add(models.Member(student_id="S3", full_name="Lapsed Larry", is_current=False))
    db.commit()
    lapsed = db.query(models.Member).filter_by(student_id="S3").one()
    code = verification.member_code(lapsed.id)
    assert client.get(f"/members/verify/{code}").json()["valid"] is False
