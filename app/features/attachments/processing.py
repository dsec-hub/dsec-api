"""Auto-compression for uploaded attachments.

Two supported kinds, each compressed losslessly-enough that the stored file is
never larger than the original:

* **image** — re-encoded to WebP (and downscaled to the media dimension cap),
  reusing the same Pillow pipeline as the media feature.
* **pdf**   — recompressed with pikepdf (stream compression + linearisation).
  If recompression doesn't actually shrink the file, the original bytes are kept.

Anything else is stored as-is (kind ``file``). Unreadable images raise
``ValueError`` (the router maps that to a 422).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from app.features.media.processing import process_image


@dataclass
class ProcessedFile:
    kind: str  # image|pdf|file
    data: bytes
    content_type: str
    ext: str
    width: int | None = None
    height: int | None = None


def is_pdf(content_type: str | None, filename: str | None) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    return bool(filename and filename.lower().endswith(".pdf"))


def is_image(content_type: str | None) -> bool:
    return bool(content_type and content_type.startswith("image/"))


def compress_pdf(data: bytes) -> bytes:
    """Recompress a PDF with pikepdf; fall back to the original if no smaller.

    pikepdf is imported lazily so the dependency is only needed when a PDF is
    actually uploaded.
    """
    try:
        import pikepdf
    except ImportError:  # dependency missing — store as-is rather than fail
        return data

    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            buf = io.BytesIO()
            pdf.save(
                buf,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                linearize=True,
            )
        out = buf.getvalue()
    except Exception:  # noqa: BLE001 — a malformed PDF shouldn't 500 the upload
        return data
    return out if 0 < len(out) < len(data) else data


def process_attachment(
    data: bytes, *, content_type: str | None, filename: str | None
) -> ProcessedFile:
    if not data:
        raise ValueError("empty upload")

    if is_image(content_type):
        processed = process_image(data)  # raises ValueError on bad image
        return ProcessedFile(
            kind="image",
            data=processed.webp_bytes,
            content_type="image/webp",
            ext="webp",
            width=processed.width,
            height=processed.height,
        )

    if is_pdf(content_type, filename):
        return ProcessedFile(
            kind="pdf",
            data=compress_pdf(data),
            content_type="application/pdf",
            ext="pdf",
        )

    raise ValueError("unsupported file type — upload a PDF or an image")
