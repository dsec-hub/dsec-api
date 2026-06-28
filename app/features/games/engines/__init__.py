"""Engine registry — the one place to add a game.

Adding a third game later is: drop a new `engines/<slug>.py` implementing
`GameEngine`, then add ONE line to REGISTRY below. Nothing else in the platform
changes (the service/router/leaderboard/draw are all engine-agnostic).
"""

from __future__ import annotations

from .base import GameEngine
from .codle import CodleEngine
from .flappy_duck import FlappyDuckEngine

REGISTRY: dict[str, GameEngine] = {
    engine.slug: engine
    for engine in (
        FlappyDuckEngine(),
        CodleEngine(),
    )
}


def get_engine(slug: str) -> GameEngine | None:
    return REGISTRY.get(slug)


__all__ = ["GameEngine", "REGISTRY", "get_engine"]
