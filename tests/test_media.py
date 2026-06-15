"""Tests for the media feature: Pillow processing + the /media route guards.

No external services: the Pillow pipeline runs locally, and the upload route is
exercised up to the Supabase boundary (unconfigured in tests → 503), which
proves processing runs end-to-end without hitting the network.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app import models
from app.core.apikeys import generate_key
from app.features.media import processing


def _png_bytes(size=(3000, 1500), color=(230, 30, 99)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def rw_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="rw", prefix=gen.prefix, key_hash=gen.key_hash,
                         scopes=["read", "write"]))
    db.commit()
    return gen.raw_key


@pytest.fixture
def ro_key(db):
    gen = generate_key()
    db.add(models.APIKey(name="ro", prefix=gen.prefix, key_hash=gen.key_hash, scopes=["read"]))
    db.commit()
    return gen.raw_key


def _h(key):
    return {"Authorization": f"Bearer {key}"}


# --- Pillow processing ------------------------------------------------------

def test_process_image_emits_webp_and_png_capped():
    result = processing.process_image(_png_bytes((3000, 1500)))
    # Longest side downscaled to the 2000px cap, aspect preserved.
    assert (result.width, result.height) == (2000, 1000)
    # PNG magic.
    assert result.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    # WebP magic (RIFF....WEBP).
    assert result.webp_bytes[:4] == b"RIFF" and result.webp_bytes[8:12] == b"WEBP"
    # Both decode back to the expected size.
    assert Image.open(io.BytesIO(result.webp_bytes)).size == (2000, 1000)
    assert Image.open(io.BytesIO(result.png_bytes)).size == (2000, 1000)


def test_process_image_does_not_upscale_small():
    result = processing.process_image(_png_bytes((400, 300)))
    assert (result.width, result.height) == (400, 300)


def test_process_image_rejects_non_image():
    with pytest.raises(ValueError):
        processing.process_image(b"this is not an image")


# --- /media route guards ----------------------------------------------------

def test_media_list_empty(client, ro_key):
    r = client.get("/media", params={"entity_type": "event", "entity_id": 1}, headers=_h(ro_key))
    assert r.status_code == 200
    assert r.json() == []


def test_media_list_bad_entity_type(client, ro_key):
    r = client.get("/media", params={"entity_type": "nope", "entity_id": 1}, headers=_h(ro_key))
    assert r.status_code == 422


def test_media_upload_requires_write_scope(client, ro_key):
    r = client.post(
        "/media",
        data={"entity_type": "event", "entity_id": "1", "role": "banner"},
        files={"file": ("t.png", _png_bytes((100, 100)), "image/png")},
        headers=_h(ro_key),
    )
    assert r.status_code == 403


def test_media_upload_bad_role(client, rw_key):
    r = client.post(
        "/media",
        data={"entity_type": "event", "entity_id": "1", "role": "logo"},
        files={"file": ("t.png", _png_bytes((100, 100)), "image/png")},
        headers=_h(rw_key),
    )
    assert r.status_code == 422


def test_media_upload_rejects_non_image_content_type(client, rw_key):
    r = client.post(
        "/media",
        data={"entity_type": "event", "entity_id": "1", "role": "banner"},
        files={"file": ("t.txt", b"hello", "text/plain")},
        headers=_h(rw_key),
    )
    assert r.status_code == 422


def test_media_upload_runs_pipeline_then_503_when_storage_unconfigured(client, rw_key):
    """A valid image passes content/role/size checks and the Pillow pipeline,
    then fails at the storage boundary (Supabase unset in tests) → 503. Proves
    the whole upload path is wired without needing the network."""
    r = client.post(
        "/media",
        data={"entity_type": "event", "entity_id": "1", "role": "banner",
              "alt_text": "hero"},
        files={"file": ("hero.png", _png_bytes((1200, 675)), "image/png")},
        headers=_h(rw_key),
    )
    assert r.status_code == 503
