"""Getting JSON out of a text completion, without guessing.

Models wrap JSON in prose or fences often enough that a bare ``json.loads``
is not good enough. Everything here is recovery of text the model actually
produced — if no parseable object is present, the caller is told so rather
than handed something invented.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None

    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    candidates.extend(match.group(1).strip() for match in _FENCE.finditer(text))

    balanced = _first_balanced_object(text)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_balanced_object(text: str) -> str | None:
    """Find the first complete ``{...}``, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
