"""Canonical block contract for custom pages + a defensive sanitizer.

A custom page's body lives in ``Document.content_json`` as::

    {"version": 1, "blocks": [ {"id": str, "type": str, ...fields}, ... ]}

This module is the SINGLE SOURCE OF TRUTH for what a block may contain. The hub
authors blocks in this shape, and the public website feed pipes every page's
blocks through :func:`sanitize_blocks` before returning them — so even a
forged/garbled ``content_json`` can only ever yield a known block type with
known, type-checked fields and http(s)-only URLs. Free-form text (markdown,
titles) is passed through verbatim and rendered *safely* by the website's
sanitizing Markdown component / React's text escaping — never as raw HTML here.

Keep this in lock-step with:
  * dsec-hub   src/lib/page-blocks.ts          (editor types + client guard)
  * dsec-website src/lib/page-blocks.ts        (renderer types + client guard)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# --- vocabularies ---------------------------------------------------------- #

BLOCK_TYPES = {
    "hero", "heading", "richtext", "quote",          # heroes & text
    "image", "gallery", "embed",                     # images & media
    "split", "columns", "cards", "divider",          # layout
    "cta", "stats", "faq", "logos",                  # marketing
}
ACCENTS = {"blue", "pink", "yellow", "mint", "sky", "violet", "lime", "coral"}
BUTTON_VARIANTS = {"pink", "ghost", "blue", "mint", "void"}
ALIGNS = {"left", "center"}
EMBED_PROVIDERS = {"youtube", "vimeo", "iframe"}
EMBED_RATIOS = {"16:9", "4:3", "1:1"}
IMAGE_WIDTHS = {"full", "wide", "inset"}
HERO_VARIANTS = {"banner", "plain"}
DIVIDER_VARIANTS = {"line", "space"}

MAX_BLOCKS = 200
MAX_ITEMS = 60          # gallery images / cards / stats / faq / logos per block
MAX_BUTTONS = 4
MAX_COLUMNS = 4
MAX_TEXT = 20_000       # per markdown/string field (defensive)

_EMBED_HOSTS = {
    "youtube": {"youtube.com", "www.youtube.com", "youtu.be", "www.youtu.be",
                "youtube-nocookie.com", "www.youtube-nocookie.com"},
    "vimeo": {"vimeo.com", "www.vimeo.com", "player.vimeo.com"},
}


# --- field coercers -------------------------------------------------------- #

def _str(v: Any, *, max_len: int = MAX_TEXT) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:max_len] if v else None


def _enum(v: Any, allowed: set[str]) -> str | None:
    return v if isinstance(v, str) and v in allowed else None


def _bool(v: Any) -> bool:
    return v is True


def _int(v: Any, *, lo: int, hi: int) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return max(lo, min(hi, v))
    return None


def _http_url(v: Any) -> str | None:
    """Allow only http/https absolute URLs (image/embed/logo sources)."""
    s = _str(v, max_len=2048)
    if not s:
        return None
    try:
        p = urlparse(s)
    except ValueError:
        return None
    return s if p.scheme in {"http", "https"} and p.netloc else None


def _link_url(v: Any) -> str | None:
    """Allow http(s) absolute links, site-relative paths, mailto and tel for buttons."""
    s = _str(v, max_len=2048)
    if not s:
        return None
    if s.startswith("/") and not s.startswith("//"):
        return s
    try:
        p = urlparse(s)
    except ValueError:
        return None
    if p.scheme in {"http", "https"} and p.netloc:
        return s
    if p.scheme in {"mailto", "tel"} and p.path:
        return s
    return None


def _image(v: Any) -> dict | None:
    """An ImageRef {mediaId?, webp, png?, alt?, width?, height?}. webp is required."""
    if not isinstance(v, dict):
        return None
    webp = _http_url(v.get("webp"))
    if not webp:
        return None
    out: dict[str, Any] = {"webp": webp}
    png = _http_url(v.get("png"))
    if png:
        out["png"] = png
    media_id = _int(v.get("mediaId"), lo=1, hi=2_000_000_000)
    if media_id is not None:
        out["mediaId"] = media_id
    alt = _str(v.get("alt"), max_len=512)
    if alt:
        out["alt"] = alt
    width = _int(v.get("width"), lo=1, hi=20_000)
    if width is not None:
        out["width"] = width
    height = _int(v.get("height"), lo=1, hi=20_000)
    if height is not None:
        out["height"] = height
    return out


def _button(v: Any) -> dict | None:
    if not isinstance(v, dict):
        return None
    label = _str(v.get("label"), max_len=120)
    href = _link_url(v.get("href"))
    if not label or not href:
        return None
    out = {"label": label, "href": href}
    variant = _enum(v.get("variant"), BUTTON_VARIANTS)
    if variant:
        out["variant"] = variant
    return out


def _buttons(v: Any) -> list[dict]:
    if not isinstance(v, list):
        return []
    out = [b for b in (_button(x) for x in v[:MAX_BUTTONS]) if b]
    return out


def _list(v: Any, fn, *, limit: int = MAX_ITEMS) -> list:
    if not isinstance(v, list):
        return []
    return [item for item in (fn(x) for x in v[:limit]) if item]


def _prune(d: dict) -> dict:
    """Drop keys whose value is None/"" so the payload stays compact."""
    return {k: val for k, val in d.items() if val not in (None, "", [], {})}


# --- per-type sanitizers --------------------------------------------------- #

def _hero(b: dict) -> dict:
    body = _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "subtitle": _str(b.get("subtitle"), max_len=600),
        "image": _image(b.get("image")),
        "align": _enum(b.get("align"), ALIGNS),
        "variant": _enum(b.get("variant"), HERO_VARIANTS),
        "buttons": _buttons(b.get("buttons")),
    })
    # Settings (align/variant) alone aren't content — drop a hero with nothing to show.
    if not any(body.get(k) for k in ("eyebrow", "title", "subtitle", "image", "buttons")):
        return {}
    return body


def _heading(b: dict) -> dict:
    body = _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "subtitle": _str(b.get("subtitle"), max_len=600),
        "align": _enum(b.get("align"), ALIGNS),
    })
    if not any(body.get(k) for k in ("eyebrow", "title", "subtitle")):
        return {}
    return body


def _richtext(b: dict) -> dict:
    return _prune({"markdown": _str(b.get("markdown"))})


def _quote(b: dict) -> dict:
    return _prune({
        "text": _str(b.get("text"), max_len=2000),
        "attribution": _str(b.get("attribution"), max_len=200),
    })


def _image_block(b: dict) -> dict:
    image = _image(b.get("image"))
    if not image:  # an image block with no image is nothing to render
        return {}
    return _prune({
        "image": image,
        "caption": _str(b.get("caption"), max_len=600),
        "width": _enum(b.get("width"), IMAGE_WIDTHS),
    })


def _gallery(b: dict) -> dict:
    images = _list(b.get("images"), _image)
    if not images:
        return {}
    return _prune({
        "images": images,
        "columns": _int(b.get("columns"), lo=2, hi=MAX_COLUMNS),
    })


def _embed(b: dict) -> dict:
    provider = _enum(b.get("provider"), EMBED_PROVIDERS) or "iframe"
    url = _http_url(b.get("url"))
    if url and provider in _EMBED_HOSTS:
        host = (urlparse(url).hostname or "").lower()
        if host not in _EMBED_HOSTS[provider]:
            url = None  # provider/host mismatch
    if not url:
        return {}  # an embed with no valid URL is useless → block dropped
    return _prune({
        "provider": provider,
        "url": url,
        "caption": _str(b.get("caption"), max_len=600),
        "ratio": _enum(b.get("ratio"), EMBED_RATIOS),
    })


def _split(b: dict) -> dict:
    body = _prune({
        "image": _image(b.get("image")),
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "markdown": _str(b.get("markdown")),
        "imageSide": _enum(b.get("imageSide"), {"left", "right"}),
        "buttons": _buttons(b.get("buttons")),
    })
    if not any(body.get(k) for k in ("image", "eyebrow", "title", "markdown", "buttons")):
        return {}
    return body


def _columns(b: dict) -> dict:
    def _col(c: Any) -> dict | None:
        if not isinstance(c, dict):
            return None
        md = _str(c.get("markdown"))
        return {"markdown": md} if md else None
    return _prune({"columns": _list(b.get("columns"), _col, limit=MAX_COLUMNS)})


def _cards(b: dict) -> dict:
    def _card(c: Any) -> dict | None:
        if not isinstance(c, dict):
            return None
        out = _prune({
            "title": _str(c.get("title"), max_len=200),
            "body": _str(c.get("body"), max_len=1200),
            "image": _image(c.get("image")),
            "href": _link_url(c.get("href")),
            "accent": _enum(c.get("accent"), ACCENTS),
        })
        return out if (out.get("title") or out.get("body") or out.get("image")) else None
    body = _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "cards": _list(b.get("cards"), _card),
        "columns": _int(b.get("columns"), lo=2, hi=MAX_COLUMNS),
    })
    if not (body.get("cards") or body.get("title") or body.get("eyebrow")):
        return {}
    return body


def _divider(b: dict) -> dict:
    # A divider is always meaningful (a visual separator) — default to a line.
    return {"variant": _enum(b.get("variant"), DIVIDER_VARIANTS) or "line"}


def _cta(b: dict) -> dict:
    body = _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "body": _str(b.get("body"), max_len=1200),
        "buttons": _buttons(b.get("buttons")),
        "align": _enum(b.get("align"), ALIGNS),
    })
    if not any(body.get(k) for k in ("eyebrow", "title", "body", "buttons")):
        return {}
    return body


def _stats(b: dict) -> dict:
    def _stat(s: Any) -> dict | None:
        if not isinstance(s, dict):
            return None
        value = _str(s.get("value"), max_len=40)
        label = _str(s.get("label"), max_len=120)
        if not value and not label:
            return None
        return _prune({"value": value, "label": label,
                       "accent": _enum(s.get("accent"), ACCENTS)})
    items = _list(b.get("items"), _stat)
    if not items:
        return {}
    return _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "items": items,
    })


def _faq(b: dict) -> dict:
    def _qa(s: Any) -> dict | None:
        if not isinstance(s, dict):
            return None
        q = _str(s.get("q"), max_len=400)
        a = _str(s.get("a"))
        return {"q": q, "a": a} if q and a else None
    items = _list(b.get("items"), _qa)
    if not items:
        return {}
    return _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "items": items,
    })


def _logos(b: dict) -> dict:
    def _logo(s: Any) -> dict | None:
        if not isinstance(s, dict):
            return None
        image = _image(s.get("image"))
        if not image:
            return None
        return _prune({
            "name": _str(s.get("name"), max_len=200),
            "image": image,
            "href": _link_url(s.get("href")),
        })
    items = _list(b.get("items"), _logo)
    if not items:
        return {}
    return _prune({
        "eyebrow": _str(b.get("eyebrow"), max_len=120),
        "title": _str(b.get("title"), max_len=300),
        "items": items,
        "marquee": _bool(b.get("marquee")),
    })


_SANITIZERS = {
    "hero": _hero, "heading": _heading, "richtext": _richtext, "quote": _quote,
    "image": _image_block, "gallery": _gallery, "embed": _embed,
    "split": _split, "columns": _columns, "cards": _cards, "divider": _divider,
    "cta": _cta, "stats": _stats, "faq": _faq, "logos": _logos,
}


def sanitize_blocks(content_json: Any) -> list[dict]:
    """Return a clean, render-safe list of blocks from a stored ``content_json``.

    Accepts either the wrapped ``{"version", "blocks": [...]}`` object or a bare
    list of blocks. Unknown block types and blocks that sanitize to nothing
    (e.g. an image block with no valid image) are dropped. Each surviving block
    keeps its original ``id`` (or gets a positional fallback) so the renderer can
    key it stably.
    """
    if isinstance(content_json, dict):
        raw = content_json.get("blocks")
    else:
        raw = content_json
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, item in enumerate(raw[:MAX_BLOCKS]):
        if not isinstance(item, dict):
            continue
        btype = item.get("type")
        if btype not in BLOCK_TYPES:
            continue
        body = _SANITIZERS[btype](item)
        if not body:
            continue  # block had no usable content
        bid = _str(item.get("id"), max_len=64) or f"b{i}"
        out.append({"id": bid, "type": btype, **body})
    return out
