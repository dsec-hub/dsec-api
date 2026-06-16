"""Regenerate the committed reference LLM guide at the repo root (``llm.md``).

The per-key guide is served live at ``GET /mcp-setup/llm?scopes=…`` and the
dashboard offers a tailored download whenever someone mints a token. This script
writes a full-scope copy (read + write + AI) into the repo as a browsable
reference so the guide is visible without hitting the API.

Re-run it whenever the tool catalogue (``app/features/mcp/catalog.py``) changes:

    .venv/bin/python scripts/gen_llm_guide.py

It imports only the catalogue + renderer (no database, no network).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.features.mcp.guide import build_llm_guide  # noqa: E402
from app.features.mcp.router import SERVER_URL  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "llm.md"


def main() -> None:
    md = build_llm_guide(
        {"read", "write", "trigger"},
        server_url=SERVER_URL,
        label="full read + write + AI access (reference copy)",
    )
    OUT.write_text(md, encoding="utf-8")
    print(f"wrote {OUT} ({len(md)} bytes)")


if __name__ == "__main__":
    main()
