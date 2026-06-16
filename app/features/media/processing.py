"""Image processing with Pillow — size-budgeted derivatives.

Takes raw uploaded bytes (already cropped client-side) and produces two
normalized derivatives, each squeezed under a hard byte budget:

* **WebP** — the image the apps / website actually render. Budget:
  ``settings.MEDIA_WEBP_MAX_BYTES`` (default ~100 KB).
* **download** — the copy offered for download (never shown on screen). A
  **JPEG** for opaque images (keeps full resolution at high quality) and a
  **PNG** for transparent logos (JPEG can't hold alpha). Budget:
  ``settings.MEDIA_DOWNLOAD_MAX_BYTES`` (default ~200 KB).

Both are EXIF-auto-oriented and downscaled so the longest side is at most
``settings.MEDIA_MAX_DIMENSION``. To honour the budgets we step *down*: first
quality (WebP/JPEG) / colour count (PNG), then the pixel dimensions, returning
the first variant that fits — or the smallest one we managed to produce.
Invalid / non-image bytes raise ``ValueError`` (the router maps that to a 422).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from app.config import settings

# Teach Pillow to read Apple HEIC/HEIF (iPhone photos). dsec-app already converts
# HEIC → JPEG client-side before upload, so this is defence in depth — it keeps
# the endpoint correct for any direct API upload too. Safe at import time: it only
# registers a decoder, nothing more.
register_heif_opener()

# Longest-side caps we fall back through when an image won't fit its byte budget
# at full size (clamped to MEDIA_MAX_DIMENSION per call). A smaller display image
# is an acceptable trade for staying under budget. The low rungs only ever bite
# for genuinely photographic content, which PNG compresses badly — dropping
# resolution degrades far more gracefully there than crushing the palette.
_DIMENSION_LADDER = (2000, 1600, 1280, 1024, 800, 640, 512)
# WebP quality steps tried at each dimension (sharp → soft).
_WEBP_QUALITY_LADDER = (80, 68, 58, 50, 42, 36)
# JPEG quality steps for the (opaque) download — kept high so the download stays
# crisp; only drops if the budget demands it.
_JPEG_QUALITY_LADDER = (88, 82, 76, 70, 64, 58)
# PNG palette sizes tried (after a lossless attempt) at each dimension.
_PNG_COLOUR_LADDER = (256, 128, 64, 32, 16)

_ALPHA_MODES = ("RGBA", "LA", "P")


@dataclass
class ProcessedImage:
    webp_bytes: bytes
    download_bytes: bytes        # JPEG (opaque) or PNG (transparent logo)
    download_ext: str            # "jpg" | "png"
    download_content_type: str   # "image/jpeg" | "image/png"
    width: int   # pixel size of the WebP — the image the apps render
    height: int


def process_image(data: bytes, *, keep_transparency: bool = False) -> ProcessedImage:
    """Normalise an uploaded image to WebP (display) + PNG (download).

    ``keep_transparency`` keeps the alpha channel instead of flattening it onto
    white — used for sponsor logos, which must sit cleanly on any background.
    """
    if not data:
        raise ValueError("empty image")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"unreadable or unsupported image: {exc}") from exc

    # Honour EXIF orientation; the re-encode below drops EXIF entirely.
    img = ImageOps.exif_transpose(img)

    # Downscale (never upscale) so the longest side fits the cap before we even
    # start chasing the byte budgets.
    cap = settings.MEDIA_MAX_DIMENSION
    if max(img.size) > cap:
        img.thumbnail((cap, cap), Image.LANCZOS)

    webp_bytes, (width, height) = _encode_webp(
        img, keep_alpha=keep_transparency, target=settings.MEDIA_WEBP_MAX_BYTES
    )

    # Transparent logos must stay PNG (alpha); everything else downloads as a
    # JPEG, which holds a full-resolution photo far better than a budget PNG.
    target = settings.MEDIA_DOWNLOAD_MAX_BYTES
    if keep_transparency:
        download_bytes, _ = _encode_png(img, keep_alpha=True, target=target)
        download_ext, download_content_type = "png", "image/png"
    else:
        download_bytes, _ = _encode_jpeg(img, target=target)
        download_ext, download_content_type = "jpg", "image/jpeg"

    return ProcessedImage(
        webp_bytes=webp_bytes,
        download_bytes=download_bytes,
        download_ext=download_ext,
        download_content_type=download_content_type,
        width=width,
        height=height,
    )


# --- internals --------------------------------------------------------------

def _caps_for(img: Image.Image) -> list[int]:
    """Descending longest-side caps to try, starting at the image's own size."""
    longest = min(max(img.size), settings.MEDIA_MAX_DIMENSION)
    caps = [longest, *[d for d in _DIMENSION_LADDER if d < longest]]
    seen: set[int] = set()
    return [c for c in caps if not (c in seen or seen.add(c))]


def _fit_to(img: Image.Image, max_dim: int) -> Image.Image:
    """A copy whose longest side is <= max_dim (never upscales)."""
    out = img.copy()
    if max(out.size) > max_dim:
        out.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return out


def _flatten_or_rgba(img: Image.Image, keep_alpha: bool) -> Image.Image:
    """RGBA when keeping transparency, otherwise flattened onto white RGB."""
    if keep_alpha and img.mode in _ALPHA_MODES:
        return img.convert("RGBA")
    if img.mode in _ALPHA_MODES:
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def _encode_webp(
    img: Image.Image, *, keep_alpha: bool, target: int
) -> tuple[bytes, tuple[int, int]]:
    """Lowest-quality-that-fits WebP; falls back to the smallest produced."""
    best: tuple[bytes, tuple[int, int]] | None = None
    for cap in _caps_for(img):
        source = _flatten_or_rgba(_fit_to(img, cap), keep_alpha)
        for quality in _WEBP_QUALITY_LADDER:
            buf = io.BytesIO()
            source.save(buf, format="WEBP", quality=quality, method=6)
            data = buf.getvalue()
            if best is None or len(data) < len(best[0]):
                best = (data, source.size)
            if len(data) <= target:
                return data, source.size
    assert best is not None  # the ladder always runs at least once
    return best


def _encode_jpeg(img: Image.Image, *, target: int) -> tuple[bytes, tuple[int, int]]:
    """Highest-quality JPEG that fits the budget; falls back to the smallest.

    Always opaque (the caller routes transparent images to PNG instead), so we
    flatten to RGB and lean on quality — JPEG keeps a full-resolution photo
    sharp at a fraction of the equivalent PNG's size.
    """
    best: tuple[bytes, tuple[int, int]] | None = None
    for cap in _caps_for(img):
        source = _flatten_or_rgba(_fit_to(img, cap), keep_alpha=False)
        for quality in _JPEG_QUALITY_LADDER:
            buf = io.BytesIO()
            source.save(
                buf, format="JPEG", quality=quality, optimize=True, progressive=True
            )
            data = buf.getvalue()
            if best is None or len(data) < len(best[0]):
                best = (data, source.size)
            if len(data) <= target:
                return data, source.size
    assert best is not None
    return best


def _png_dump(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _quantize(im: Image.Image, colours: int) -> Image.Image:
    """Palette-reduce to `colours`, preserving alpha for transparent sources.

    Dithering is deliberately OFF: it scatters pixels and defeats PNG's
    row-based compression (a dithered low-colour PNG can be *larger* than the
    original). The PNG is a download, not the on-screen image, so flat colour
    regions — smaller files — are the right trade.
    """
    if im.mode == "RGBA":
        # FASTOCTREE is the quantizer that keeps an alpha channel.
        return im.quantize(
            colors=colours, method=Image.Quantize.FASTOCTREE, dither=Image.NONE
        )
    return im.convert(
        "P", palette=Image.ADAPTIVE, colors=colours, dither=Image.NONE
    )


def _encode_png(
    img: Image.Image, *, keep_alpha: bool, target: int
) -> tuple[bytes, tuple[int, int]]:
    """Smallest PNG that fits: lossless first, then palette-reduce, then shrink.

    The PNG is a download, not an on-screen image, so trading colour depth for
    bytes is acceptable — display quality lives in the WebP.
    """
    best: tuple[bytes, tuple[int, int]] | None = None
    for cap in _caps_for(img):
        base = _flatten_or_rgba(_fit_to(img, cap), keep_alpha)

        # `None` = lossless; the rest are palette sizes. We only quantize (and
        # only as far as needed) once the lossless attempt overshoots budget.
        for colours in (None, *_PNG_COLOUR_LADDER):
            variant = base if colours is None else _quantize(base, colours)
            data = _png_dump(variant)
            if best is None or len(data) < len(best[0]):
                best = (data, base.size)
            if len(data) <= target:
                return data, base.size
    assert best is not None
    return best
