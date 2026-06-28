"""Codle answer pool — five-letter programming words/identifiers.

Kept to a fixed length (5) so the board is Wordle-shaped. All uppercase, A-Z
only. Curated to be fair: common, recognisable coding terms, no obscure jargon.
Adding words is safe — the daily answer is chosen deterministically by index, so
the puzzle for a past date never changes as long as a word keeps its position
(append new words at the end rather than inserting).
"""

from __future__ import annotations

WORDS: list[str] = [
    "ARRAY",
    "ASYNC",
    "AWAIT",
    "BREAK",
    "BUILD",
    "BYTES",
    "CACHE",
    "CLASS",
    "CONST",
    "DEBUG",
    "EMITS",
    "ENUMS",
    "EVENT",
    "FETCH",
    "FIELD",
    "FLOAT",
    "FRAME",
    "GRAPH",
    "INDEX",
    "INPUT",
    "LOGIC",
    "MACRO",
    "MERGE",
    "MODEL",
    "MOUNT",
    "PARSE",
    "PATCH",
    "PRINT",
    "PROXY",
    "QUERY",
    "QUEUE",
    "RAISE",
    "REGEX",
    "ROUTE",
    "SCOPE",
    "SHELL",
    "SLICE",
    "STACK",
    "SUPER",
    "TABLE",
    "TOKEN",
    "TUPLE",
    "TYPES",
    "WHILE",
    "YIELD",
]

WORD_SET: frozenset[str] = frozenset(WORDS)
