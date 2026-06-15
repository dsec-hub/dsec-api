"""The post-event review form template.

A short, plain-language feedback form: a quick star rating plus open questions
on what went well and what to improve next time. The questions are declarative
(`QUESTIONS`) so they're easy to tweak in one place; `build_blocks` turns them
into the Tally "blocks" payload (see developers.tally.so).

Tally model (verified against the live API): EVERY block gets its own unique
`groupUuid` — a `TITLE` must NOT share a group with its input block (only the
options of a single multiple-choice question share a group, which we don't use
here). `groupType` matches the block's own kind (TITLE→"QUESTION",
TEXTAREA→"TEXTAREA", RATING→"RATING", …). Rich-text payloads use
`safeHTMLSchema` (a `[["text"]]` nested array), not an `html` string. The
question text strings double as the join key when mapping submissions back to
fields (see service.py), so keep them in sync with `TITLE_TO_KEY`.
"""

from __future__ import annotations

import uuid

# Intro shown under the form title.
INTRO_TEXT = (
    "Thanks for coming! This 1-minute, anonymous review helps us run better events."
)

# Declarative question set. `key` is our stable internal id; `title` is what the
# attendee sees AND the key we match submissions on. Q1 is required; rest optional.
QUESTIONS: list[dict] = [
    {
        "key": "rating",
        "title": "Overall, how would you rate this event?",
        "kind": "rating",
        "required": True,
        "scale": 5,
    },
    {
        "key": "enjoyed",
        "title": "What did you enjoy most?",
        "kind": "textarea",
        "required": False,
        "placeholder": "The talks, the people, the food…",
    },
    {
        "key": "improve",
        "title": "What could we improve for next time?",
        "kind": "textarea",
        "required": False,
        "placeholder": "Anything we could do better",
    },
    {
        "key": "return",
        "title": "How likely are you to come to another DSEC event? (1 = not likely, 5 = very likely)",
        "kind": "linear_scale",
        "required": False,
        "min": 1,
        "max": 5,
    },
    {
        "key": "comments",
        "title": "Anything else you'd like to tell us?",
        "kind": "textarea",
        "required": False,
        "placeholder": "Optional",
    },
]

# question title -> internal key, for mapping Tally submissions back to fields.
TITLE_TO_KEY: dict[str, str] = {q["title"]: q["key"] for q in QUESTIONS}


def _rich(text: str) -> list[list[str]]:
    """Tally's rich-text payload format — a nested array of text runs."""
    return [[text]]


def _block(type_: str, group_type: str, payload: dict) -> dict:
    # Every block gets its OWN groupUuid: Tally rejects a TITLE that shares a
    # group with an input block ("Title/Label blocks must have their own
    # groupUuid"). Only the options of one choice question share a group, which
    # this template doesn't use.
    return {
        "uuid": str(uuid.uuid4()),
        "type": type_,
        "groupUuid": str(uuid.uuid4()),
        "groupType": group_type,
        "payload": payload,
    }


def build_blocks(event_name: str) -> list[dict]:
    """Build the Tally blocks payload for one event's review form."""
    title = f"{event_name} — Post-event review"
    blocks: list[dict] = [
        _block("FORM_TITLE", "TEXT", {"safeHTMLSchema": _rich(title), "title": title}),
        _block("TEXT", "TEXT", {"safeHTMLSchema": _rich(INTRO_TEXT)}),
    ]

    # Each question = a TITLE block then its input block, each in its own group.
    for q in QUESTIONS:
        blocks.append(
            _block("TITLE", "QUESTION", {"isFolded": False, "safeHTMLSchema": _rich(q["title"])})
        )
        if q["kind"] == "rating":
            # Tally's default RATING is a 5-star scale; only isRequired is accepted.
            blocks.append(_block("RATING", "RATING", {"isRequired": q["required"]}))
        elif q["kind"] == "textarea":
            blocks.append(
                _block("TEXTAREA", "TEXTAREA",
                       {"isRequired": q["required"], "placeholder": q.get("placeholder", "")})
            )
        elif q["kind"] == "linear_scale":
            # LINEAR_SCALE accepts start/end/step only (no custom end labels via API).
            blocks.append(
                _block("LINEAR_SCALE", "LINEAR_SCALE", {
                    "isRequired": q["required"],
                    "start": q["min"],
                    "end": q["max"],
                    "step": 1,
                })
            )
        else:  # pragma: no cover — guards a typo in QUESTIONS
            raise ValueError(f"unknown question kind: {q['kind']!r}")

    return blocks
