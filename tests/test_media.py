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

def test_process_image_emits_webp_and_download_capped():
    result = processing.process_image(_png_bytes((3000, 1500)))
    # Longest side downscaled to the 2000px cap, aspect preserved.
    assert (result.width, result.height) == (2000, 1000)
    # Opaque source → JPEG download.
    assert result.download_ext == "jpg"
    assert result.download_content_type == "image/jpeg"
    assert result.download_bytes[:3] == b"\xff\xd8\xff"  # JPEG magic
    # WebP magic (RIFF....WEBP).
    assert result.webp_bytes[:4] == b"RIFF" and result.webp_bytes[8:12] == b"WEBP"
    # Both decode back to the expected size.
    assert Image.open(io.BytesIO(result.webp_bytes)).size == (2000, 1000)
    assert Image.open(io.BytesIO(result.download_bytes)).size == (2000, 1000)


def test_process_image_does_not_upscale_small():
    result = processing.process_image(_png_bytes((400, 300)))
    assert (result.width, result.height) == (400, 300)


def test_process_image_rejects_non_image():
    with pytest.raises(ValueError):
        processing.process_image(b"this is not an image")


def _detailed_png_bytes(size=(2400, 2400)) -> bytes:
    """A high-detail image whose lossless PNG is multi-MB — the worst case the
    size-budget ladder has to tame (stands in for a real photo)."""
    img = Image.effect_mandelbrot(size, (-2.0, -1.5, 1.0, 1.5), 120).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_budgets_enforced_for_detailed_image():
    """A detailed photo-like image is squeezed under both byte budgets."""
    from app.config import settings

    raw = _detailed_png_bytes()
    assert len(raw) > settings.MEDIA_DOWNLOAD_MAX_BYTES  # source genuinely oversized

    result = processing.process_image(raw)
    assert len(result.webp_bytes) <= settings.MEDIA_WEBP_MAX_BYTES
    assert len(result.download_bytes) <= settings.MEDIA_DOWNLOAD_MAX_BYTES
    assert result.download_ext == "jpg"  # opaque photo → JPEG download
    # The WebP decodes at the reported (display) dimensions.
    assert Image.open(io.BytesIO(result.webp_bytes)).size == (result.width, result.height)


def test_budget_step_down_shrinks_dimensions(monkeypatch):
    """When even the smallest quality won't fit, the ladder drops resolution."""
    from app.config import settings

    monkeypatch.setattr(settings, "MEDIA_WEBP_MAX_BYTES", 8_000)
    monkeypatch.setattr(settings, "MEDIA_DOWNLOAD_MAX_BYTES", 8_000)

    result = processing.process_image(_detailed_png_bytes((2000, 2000)))
    assert len(result.webp_bytes) <= settings.MEDIA_WEBP_MAX_BYTES
    # An 8 KB ceiling is unreachable at 2000px — it must have downscaled.
    assert max(result.width, result.height) < 2000


def test_logo_download_is_png_opaque_download_is_jpeg():
    """Transparent logos download as PNG (alpha); opaque images as JPEG."""
    logo = processing.process_image(_rgba_png_bytes(), keep_transparency=True)
    assert logo.download_ext == "png"
    assert logo.download_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    # The semi-transparent source (alpha 128) must stay non-opaque.
    alpha = Image.open(io.BytesIO(logo.download_bytes)).convert("RGBA").getchannel("A")
    assert min(alpha.getdata()) < 255

    opaque = processing.process_image(_png_bytes((300, 300)))
    assert opaque.download_ext == "jpg"
    assert opaque.download_bytes[:3] == b"\xff\xd8\xff"


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
        data={"entity_type": "event", "entity_id": "1", "role": "bogus"},
        files={"file": ("t.png", _png_bytes((100, 100)), "image/png")},
        headers=_h(rw_key),
    )
    assert r.status_code == 422


def test_media_upload_accepts_sponsor_logo_and_speaker_photo(client, rw_key):
    """sponsor/logo and speaker/photo are valid now — they pass the guards and
    the Pillow pipeline, stopping only at the (unconfigured) storage boundary."""
    for entity_type, role in (("sponsor", "logo"), ("speaker", "photo")):
        r = client.post(
            "/media",
            data={"entity_type": entity_type, "entity_id": "1", "role": role},
            files={"file": ("t.png", _png_bytes((300, 300)), "image/png")},
            headers=_h(rw_key),
        )
        assert r.status_code == 503, (entity_type, role, r.status_code)


def _rgba_png_bytes(size=(200, 200)) -> bytes:
    buf = io.BytesIO()
    # A semi-transparent square — alpha must survive logo processing.
    Image.new("RGBA", size, (230, 30, 99, 128)).save(buf, format="PNG")
    return buf.getvalue()


def test_logo_webp_keeps_transparency():
    """Default WebP flattens onto white; logos must keep their alpha channel."""
    flat = processing.process_image(_rgba_png_bytes())
    assert Image.open(io.BytesIO(flat.webp_bytes)).mode == "RGB"  # flattened to white

    logo = processing.process_image(_rgba_png_bytes(), keep_transparency=True)
    assert Image.open(io.BytesIO(logo.webp_bytes)).mode == "RGBA"  # alpha preserved


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


# --- storage backend selection ----------------------------------------------
#
# The binaries moved from Supabase Storage to Cloudflare R2. Both backends sit
# behind the same three functions, and `media_asset` stores the object path
# separately from the public URL — so switching backends only changes the URL
# prefix, and the paths carry over untouched. These pin the parts that are easy
# to get silently wrong: which backend a given setting selects, and that an
# unconfigured backend fails loudly at the boundary instead of writing a broken
# URL into the database.

from app.features.media import storage  # noqa: E402


def test_r2_refuses_when_unconfigured_and_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "r2")
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                "R2_PUBLIC_BASE_URL"):
        monkeypatch.setattr(storage.settings, var, "")
    storage._r2_client.cache_clear()
    with pytest.raises(storage.StorageNotConfigured) as exc:
        storage.upload_object("event/1/a.webp", b"x", "image/webp")
    msg = str(exc.value)
    # Every missing name is listed — a partial config should say which half.
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        assert var in msg
    storage._r2_client.cache_clear()


def test_r2_requires_public_base_url_even_when_credentials_are_present(monkeypatch):
    """A bucket is private until explicitly exposed, so the public base URL is
    not derivable from the account id. Without it the app would upload happily
    and write an unreachable URL — a failure that only shows up later as a
    broken image in a browser."""
    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "r2")
    monkeypatch.setattr(storage.settings, "R2_ACCOUNT_ID", "acct")
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "key")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(storage.settings, "R2_PUBLIC_BASE_URL", "")
    storage._r2_client.cache_clear()
    with pytest.raises(storage.StorageNotConfigured) as exc:
        storage.upload_object("event/1/a.webp", b"x", "image/webp")
    assert "R2_PUBLIC_BASE_URL" in str(exc.value)
    storage._r2_client.cache_clear()


def test_public_url_joins_cleanly_regardless_of_slashes(monkeypatch):
    monkeypatch.setattr(storage.settings, "R2_PUBLIC_BASE_URL", "https://media.dsec.club/")
    assert storage._r2_public_url("event/1/a.webp") == "https://media.dsec.club/event/1/a.webp"
    monkeypatch.setattr(storage.settings, "R2_PUBLIC_BASE_URL", "https://media.dsec.club")
    assert storage._r2_public_url("/event/1/a.webp") == "https://media.dsec.club/event/1/a.webp"


def test_backend_dispatch_selects_the_configured_one(monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "_r2_upload", lambda p, d, c: calls.append("r2") or "r2-url")
    monkeypatch.setattr(storage, "_supabase_upload", lambda p, d, c: calls.append("supa") or "supa-url")

    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "r2")
    assert storage.upload_object("p", b"x", "image/webp") == "r2-url"
    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "supabase")
    assert storage.upload_object("p", b"x", "image/webp") == "supa-url"
    # Unset/unknown must fall back to Supabase, not blow up mid-request.
    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "")
    assert storage.upload_object("p", b"x", "image/webp") == "supa-url"
    assert calls == ["r2", "supa", "supa"]


def test_delete_is_best_effort_and_never_raises(monkeypatch):
    """Cleanup must not block deleting the owning DB row."""
    monkeypatch.setattr(storage.settings, "STORAGE_BACKEND", "r2")
    def boom(paths): raise RuntimeError("r2 down")
    monkeypatch.setattr(storage, "_r2_delete", boom)
    storage.delete_objects(["a/b.webp"])          # must not raise
    storage.delete_objects([])                     # no-op, must not call the backend
    storage.delete_objects(["", None])             # filtered out
