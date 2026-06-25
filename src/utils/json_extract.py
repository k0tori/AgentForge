"""Shared JSON extraction and repair utilities for LLM responses."""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict:
    """Extract JSON object from LLM response text.

    Handles common LLM JSON errors:
    - JSON in markdown code fences
    - Missing commas between properties
    - Trailing commas

    Args:
        text: Raw LLM response text

    Returns:
        Parsed JSON as dict

    Raises:
        ValueError: If no valid JSON found
    """
    # Try markdown code fence first
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        json_str = match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            repaired = repair_json(json_str)
            return json.loads(repaired)

    # Find raw JSON object using bracket counting
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {text[:200]}")

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                json_str = text[start : i + 1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    repaired = repair_json(json_str)
                    return json.loads(repaired)

    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


def repair_json(json_str: str) -> str:
    """Attempt to repair common JSON formatting issues from LLM output.

    Fixes:
    - Missing commas between object properties
    - Trailing commas before } or ]

    Args:
        json_str: Potentially malformed JSON string

    Returns:
        Repaired JSON string
    """
    # Fix trailing commas: ,} → } and ,] → ]
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)

    # Fix missing commas between properties:
    # Pattern: "value" followed by newline and whitespace then "key"
    json_str = re.sub(
        r'("(?:[^"\\]|\\.)*")\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)',
        r'\1,\n\2',
        json_str,
    )

    # Fix missing commas after closing } or ] before next property
    json_str = re.sub(r'([}\]])\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)', r'\1,\n\2', json_str)

    # Fix missing commas after values (number, boolean, null) before next property
    json_str = re.sub(
        r'(true|false|null|\d+(?:\.\d+)?)\s*\n\s*("(?:[^"\\]|\\.)*"\s*:)',
        r'\1,\n\2',
        json_str,
    )

    return json_str
