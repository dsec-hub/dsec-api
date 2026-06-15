"""Image processing with Pillow.

Takes raw uploaded bytes (already cropped client-side) and produces two
normalized derivatives:

* **WebP** — compressed, for fast web display (what dsec-app / dsec-website show)
* **PNG**  — lossless-ish, offered as the download

Both are EXIF-auto-oriented and downscaled so the longest side is at most
`settings.MEDIA_MAX_DIMENSION`. Invalid / non-image bytes raise ``ValueError``
(the router maps that to a 422).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings


@dataclass
class ProcessedImage:
    webp_bytes: bytes
    png_bytes: bytes
    width: int
    height: int


def process_image(data: bytes) -> ProcessedImage:
    if not data:
        raise ValueError("empty image")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"unreadable or unsupported image: {exc}") from exc

    # Honour EXIF orientation, then drop EXIF by working on a clean copy.
    img = ImageOps.exif_transpose(img)

    # Downscale (never upscale) so the longest side fits the cap.
    cap = settings.MEDIA_MAX_DIMENSION
    if max(img.size) > cap:
        img.thumbnail((cap, cap), Image.LANCZOS)

    width, height = img.size

    # PNG keeps alpha; WebP flattens onto white when the source has transparency
    # (keeps file size predictable and avoids dark-mode halos).
    png_source = img if img.mode in ("RGBA", "LA", "P") else img.convert("RGBA")
    png_buf = io.BytesIO()
    png_source.convert("RGBA").save(png_buf, format="PNG", optimize=True)

    if img.mode in ("RGBA", "LA", "P"):
        flattened = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        flattened.paste(rgba, mask=rgba.split()[-1])
        webp_source = flattened
    else:
        webp_source = img.convert("RGB")
    webp_buf = io.BytesIO()
    webp_source.save(webp_buf, format="WEBP", quality=82, method=6)

    return ProcessedImage(
        webp_bytes=webp_buf.getvalue(),
        png_bytes=png_buf.getvalue(),
        width=width,
        height=height,
    )
